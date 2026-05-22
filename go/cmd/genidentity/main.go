package main

import (
	"encoding/base64"
	"flag"
	"fmt"
	"log"
	"os"
	"path/filepath"

	"github.com/libp2p/go-libp2p/core/crypto"
	"github.com/libp2p/go-libp2p/core/peer"
)

const defaultKeyFile = "libp2p_identity.key"
const defaultPubFile = "libp2p_identity.pub"

func loadOrCreateIdentity(path string) (crypto.PrivKey, bool, error) {
	data, err := os.ReadFile(path)
	if err == nil {
		decoded, err := base64.StdEncoding.DecodeString(string(data))
		if err != nil {
			return nil, false, fmt.Errorf("invalid identity file %q: %w", path, err)
		}
		priv, err := crypto.UnmarshalPrivateKey(decoded)
		if err != nil {
			return nil, false, fmt.Errorf("failed to unmarshal private key %q: %w", path, err)
		}
		return priv, false, nil
	}
	if !os.IsNotExist(err) {
		return nil, false, err
	}

	priv, _, err := crypto.GenerateEd25519Key(nil)
	if err != nil {
		return nil, false, fmt.Errorf("failed to generate identity: %w", err)
	}
	marshaled, err := crypto.MarshalPrivateKey(priv)
	if err != nil {
		return nil, false, fmt.Errorf("failed to marshal private key: %w", err)
	}
	encoded := base64.StdEncoding.EncodeToString(marshaled)
	if err := os.WriteFile(path, []byte(encoded), 0o600); err != nil {
		return nil, false, fmt.Errorf("failed to write identity file %q: %w", path, err)
	}
	return priv, true, nil
}

func main() {
	keyPath := flag.String("key", defaultKeyFile, "path to the libp2p private key file")
	pubPath := flag.String("pub", defaultPubFile, "path to write the public key file")
	flag.Parse()

	absKeyPath, err := filepath.Abs(*keyPath)
	if err != nil {
		log.Fatalf("failed to resolve key file path: %v", err)
	}
	absPubPath, err := filepath.Abs(*pubPath)
	if err != nil {
		log.Fatalf("failed to resolve pub file path: %v", err)
	}

	priv, created, err := loadOrCreateIdentity(*keyPath)
	if err != nil {
		log.Fatalf("identity error: %v", err)
	}

	peerID, err := peer.IDFromPrivateKey(priv)
	if err != nil {
		log.Fatalf("failed to derive peer ID: %v", err)
	}

	pubMarshaled, err := crypto.MarshalPublicKey(priv.GetPublic())
	if err != nil {
		log.Fatalf("failed to marshal public key: %v", err)
	}
	pubEncoded := base64.StdEncoding.EncodeToString(pubMarshaled)
	if err := os.WriteFile(*pubPath, []byte(pubEncoded), 0o644); err != nil {
		log.Fatalf("failed to write public key file %q: %v", *pubPath, err)
	}

	status := "reused"
	if created {
		status = "created"
	}
	fmt.Printf("peerID=%s\n", peerID.String())
	fmt.Printf("status=%s\n", status)
	fmt.Printf("private key=%s\n", absKeyPath)
	fmt.Printf("public key=%s\n", absPubPath)
}
