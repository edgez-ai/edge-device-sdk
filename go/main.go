package main

import (
	"context"
	"flag"
	"fmt"
	"log"
	"net"
	"os"
	"strings"
	"sync"
	"time"

	"github.com/huin/goupnp"
	"github.com/huin/goupnp/dcps/internetgateway1"
	"github.com/huin/goupnp/dcps/internetgateway2"
	libp2p "github.com/libp2p/go-libp2p"
	"github.com/libp2p/go-libp2p/core/event"
	"github.com/libp2p/go-libp2p/core/host"
	"github.com/libp2p/go-libp2p/core/network"
	"github.com/libp2p/go-libp2p/core/peer"
	"github.com/libp2p/go-libp2p/p2p/protocol/circuitv2/client"
	"github.com/libp2p/go-libp2p/p2p/protocol/identify"
	ping "github.com/libp2p/go-libp2p/p2p/protocol/ping"
	"github.com/multiformats/go-multiaddr"
)

// isPrivateAddr checks if an address string contains a private IP
func isPrivateAddr(addr string) bool {
	return strings.Contains(addr, "/ip4/10.") ||
		strings.Contains(addr, "/ip4/172.16.") ||
		strings.Contains(addr, "/ip4/172.17.") ||
		strings.Contains(addr, "/ip4/172.18.") ||
		strings.Contains(addr, "/ip4/172.19.") ||
		strings.Contains(addr, "/ip4/172.20.") ||
		strings.Contains(addr, "/ip4/172.21.") ||
		strings.Contains(addr, "/ip4/172.22.") ||
		strings.Contains(addr, "/ip4/172.23.") ||
		strings.Contains(addr, "/ip4/172.24.") ||
		strings.Contains(addr, "/ip4/172.25.") ||
		strings.Contains(addr, "/ip4/172.26.") ||
		strings.Contains(addr, "/ip4/172.27.") ||
		strings.Contains(addr, "/ip4/172.28.") ||
		strings.Contains(addr, "/ip4/172.29.") ||
		strings.Contains(addr, "/ip4/172.30.") ||
		strings.Contains(addr, "/ip4/172.31.") ||
		strings.Contains(addr, "/ip4/192.168.") ||
		strings.Contains(addr, "/ip4/0.0.0.") ||
		strings.Contains(addr, "/ip6/fc") ||
		strings.Contains(addr, "/ip6/fd")
}

// isLoopback checks if an address string contains a loopback IP
func isLoopback(addr string) bool {
	return strings.Contains(addr, "/ip4/127.") ||
		strings.Contains(addr, "/ip6/::1")
}

// extractRelayAddrInfo returns relay AddrInfo if the target includes /p2p-circuit.
func extractRelayAddrInfo(target multiaddr.Multiaddr) (*peer.AddrInfo, error) {
	if target == nil {
		return nil, nil
	}
	addrStr := target.String()
	if !strings.Contains(addrStr, "/p2p-circuit") {
		return nil, nil
	}
	parts := strings.Split(addrStr, "/p2p-circuit")
	if len(parts) == 0 || parts[0] == "" {
		return nil, fmt.Errorf("invalid relay multiaddr in %q", addrStr)
	}
	relayMA, err := multiaddr.NewMultiaddr(parts[0])
	if err != nil {
		return nil, fmt.Errorf("invalid relay multiaddr %q: %w", parts[0], err)
	}
	relayInfo, err := peer.AddrInfoFromP2pAddr(relayMA)
	if err != nil {
		return nil, fmt.Errorf("failed to parse relay addr info: %w", err)
	}
	return relayInfo, nil
}

func reserveOnRelay(ctx context.Context, h host.Host, relayInfo *peer.AddrInfo) {
	if h == nil || relayInfo == nil {
		return
	}
	reserveCtx, cancel := context.WithTimeout(ctx, 15*time.Second)
	defer cancel()
	if err := h.Connect(reserveCtx, *relayInfo); err != nil {
		log.Printf("[RELAY] connect failed: %v", err)
		return
	}
	res, err := client.Reserve(reserveCtx, h, *relayInfo)
	if err != nil {
		log.Printf("[RELAY] reserve failed: %v", err)
		return
	}
	if len(res.Addrs) > 0 {
		log.Printf("[RELAY] reserved; relay addrs: %v", res.Addrs)
	} else {
		log.Printf("[RELAY] reserved; no relay addrs returned")
	}
}

func init() {
	// Enable debug logging for libp2p subsystems
	// Set GOLOG_LOG_LEVEL=debug to enable all, or specific subsystems:
	_ = os.Getenv("GOLOG_LOG_LEVEL")
}

func replaceTCPPort(addrStr string, port string) string {
	idx := strings.Index(addrStr, "/tcp/")
	if idx < 0 {
		return addrStr
	}
	start := idx + len("/tcp/")
	end := start
	for end < len(addrStr) {
		c := addrStr[end]
		if c < '0' || c > '9' {
			break
		}
		end++
	}
	if end == start {
		return addrStr
	}
	return addrStr[:start] + port + addrStr[end:]
}

func getLocalIPv4() (net.IP, error) {
	conn, err := net.Dial("udp4", "1.1.1.1:80")
	if err != nil {
		return nil, err
	}
	defer conn.Close()
	localAddr, ok := conn.LocalAddr().(*net.UDPAddr)
	if !ok || localAddr.IP == nil {
		return nil, fmt.Errorf("failed to determine local IP")
	}
	return localAddr.IP, nil
}

func mapUPnPPort(ctx context.Context, internalPort int, externalPort int) (net.IP, error) {
	localIP, err := getLocalIPv4()
	if err != nil {
		return nil, err
	}

	tryMap := func(root *goupnp.RootDevice, srv *goupnp.Service) (net.IP, error) {
		const desc = "libp2p"
		const ttl = 3600

		switch srv.ServiceType {
		case internetgateway2.URN_WANIPConnection_2:
			client := &internetgateway2.WANIPConnection2{ServiceClient: goupnp.ServiceClient{SOAPClient: srv.NewSOAPClient(), RootDevice: root, Service: srv}}
			_, isNat, err := client.GetNATRSIPStatusCtx(ctx)
			if err != nil || !isNat {
				return nil, fmt.Errorf("UPnP IP2 not a NAT or unavailable")
			}
			if err := client.AddPortMappingCtx(ctx, "", uint16(externalPort), "TCP", uint16(internalPort), localIP.String(), true, desc, ttl); err != nil {
				return nil, err
			}
			externalIP, err := client.GetExternalIPAddress()
			if err != nil {
				return nil, err
			}
			return net.ParseIP(externalIP), nil
		case internetgateway2.URN_WANIPConnection_1:
			client := &internetgateway2.WANIPConnection1{ServiceClient: goupnp.ServiceClient{SOAPClient: srv.NewSOAPClient(), RootDevice: root, Service: srv}}
			if _, isNat, err := client.GetNATRSIPStatusCtx(ctx); err == nil && isNat {
				if err := client.AddPortMappingCtx(ctx, "", uint16(externalPort), "TCP", uint16(internalPort), localIP.String(), true, desc, ttl); err == nil {
					if externalIP, err := client.GetExternalIPAddress(); err == nil {
						return net.ParseIP(externalIP), nil
					}
				}
			}
			clientV1 := &internetgateway1.WANIPConnection1{ServiceClient: goupnp.ServiceClient{SOAPClient: srv.NewSOAPClient(), RootDevice: root, Service: srv}}
			_, isNat, err := clientV1.GetNATRSIPStatusCtx(ctx)
			if err != nil || !isNat {
				return nil, fmt.Errorf("UPnP IP1 not a NAT or unavailable")
			}
			if err := clientV1.AddPortMappingCtx(ctx, "", uint16(externalPort), "TCP", uint16(internalPort), localIP.String(), true, desc, ttl); err != nil {
				return nil, err
			}
			externalIP, err := clientV1.GetExternalIPAddress()
			if err != nil {
				return nil, err
			}
			return net.ParseIP(externalIP), nil
		case internetgateway2.URN_WANPPPConnection_1:
			client := &internetgateway2.WANPPPConnection1{ServiceClient: goupnp.ServiceClient{SOAPClient: srv.NewSOAPClient(), RootDevice: root, Service: srv}}
			if _, isNat, err := client.GetNATRSIPStatusCtx(ctx); err == nil && isNat {
				if err := client.AddPortMappingCtx(ctx, "", uint16(externalPort), "TCP", uint16(internalPort), localIP.String(), true, desc, ttl); err == nil {
					if externalIP, err := client.GetExternalIPAddress(); err == nil {
						return net.ParseIP(externalIP), nil
					}
				}
			}
			clientV1 := &internetgateway1.WANPPPConnection1{ServiceClient: goupnp.ServiceClient{SOAPClient: srv.NewSOAPClient(), RootDevice: root, Service: srv}}
			_, isNat, err := clientV1.GetNATRSIPStatusCtx(ctx)
			if err != nil || !isNat {
				return nil, fmt.Errorf("UPnP PPP1 not a NAT or unavailable")
			}
			if err := clientV1.AddPortMappingCtx(ctx, "", uint16(externalPort), "TCP", uint16(internalPort), localIP.String(), true, desc, ttl); err != nil {
				return nil, err
			}
			externalIP, err := clientV1.GetExternalIPAddress()
			if err != nil {
				return nil, err
			}
			return net.ParseIP(externalIP), nil
		default:
			return nil, fmt.Errorf("unsupported service type")
		}
	}

	tryTargets := []string{
		internetgateway2.URN_WANConnectionDevice_2,
		internetgateway1.URN_WANConnectionDevice_1,
	}

	for _, target := range tryTargets {
		devs, err := goupnp.DiscoverDevicesCtx(ctx, target)
		if err != nil {
			continue
		}
		for _, dev := range devs {
			if dev.Err != nil {
				continue
			}
			var mappedIP net.IP
			dev.Root.Device.VisitServices(func(srv *goupnp.Service) {
				if mappedIP != nil {
					return
				}
				if ip, err := tryMap(dev.Root, srv); err == nil && ip != nil {
					mappedIP = ip
				}
			})
			if mappedIP != nil {
				return mappedIP, nil
			}
		}
	}

	return nil, fmt.Errorf("no UPnP IGD found for port mapping")
}

func main() {
	addrFlag := flag.String("addr", "", "Target multiaddr with peer ID, e.g. /ip4/0.0.0.0/tcp/9000/p2p/<peerid>")
	timeoutFlag := flag.Duration("timeout", 10*time.Second, "Dial + ping timeout")
	listenFlag := flag.String("listen", "/ip4/0.0.0.0/tcp/4001", "Local listen address for hole punching")
	flag.Parse()

	if *addrFlag == "" {
		log.Fatalf("-addr is required (e.g. /ip4/0.0.0.0/tcp/9000/p2p/<peerid>)")
	}

	maddr, err := multiaddr.NewMultiaddr(*addrFlag)
	if err != nil {
		log.Fatalf("invalid multiaddr: %v", err)
	}

	info, err := peer.AddrInfoFromP2pAddr(maddr)
	if err != nil {
		log.Fatalf("failed to parse peer info: %v", err)
	}

	relayInfo, relayErr := extractRelayAddrInfo(maddr)
	if relayErr != nil {
		log.Printf("[RELAY] warning: %v", relayErr)
	}

	// Extract relay IP (before /p2p-circuit) to avoid using relay as observed address
	relayIP := ""
	if relayInfo != nil {
		for _, raddr := range relayInfo.Addrs {
			if ip, err := raddr.ValueForProtocol(multiaddr.P_IP4); err == nil {
				relayIP = ip
				break
			}
		}
	}

	baseCtx := context.Background()

	// Track observed public addresses for DCUtR
	var addrMu sync.RWMutex
	var observedPublicAddr multiaddr.Multiaddr
	var placeholderAddr multiaddr.Multiaddr
	var placeholderPort string
	listenMA, err := multiaddr.NewMultiaddr(*listenFlag)
	if err != nil {
		log.Fatalf("invalid listen multiaddr: %v", err)
	}
	if p, err := listenMA.ValueForProtocol(multiaddr.P_TCP); err == nil {
		addrMu.Lock()
		placeholderPort = p
		addrMu.Unlock()
	}

	// Create host with hole punching enabled for DCUtR
	opts := []libp2p.Option{
		libp2p.ListenAddrStrings(*listenFlag), // Need to listen for hole punching
		libp2p.EnableRelay(),
		libp2p.EnableHolePunching(), // Enable DCUtR hole punching
		libp2p.EnableAutoNATv2(),    // Enable AutoNAT for NAT detection
		libp2p.NATPortMap(),         // Enable UPnP/NAT-PMP port mapping
		// ForceReachabilityPrivate makes the node assume it's behind NAT immediately.
		// This helps with DCUtR handler registration timing.
		libp2p.ForceReachabilityPrivate(),
		// AddrsFactory to advertise public addresses for DCUtR.
		// We need at least one public-looking address for DCUtR to initialize.
		// Once we learn our real observed address, we use ONLY that (no placeholder).
		libp2p.AddrsFactory(func(addrs []multiaddr.Multiaddr) []multiaddr.Multiaddr {
			addrMu.RLock()
			obs := observedPublicAddr
			ph := placeholderAddr
			port := placeholderPort
			addrMu.RUnlock()
			if obs == nil {
				result := append([]multiaddr.Multiaddr{}, addrs...)
				if ph == nil {
					if port == "" || port == "0" {
						for _, addr := range addrs {
							if p, err := addr.ValueForProtocol(multiaddr.P_TCP); err == nil && p != "0" {
								port = p
								break
							}
						}
					}
					if port != "" && port != "0" {
						if phAddr, err := multiaddr.NewMultiaddr("/ip4/1.2.3.4/tcp/" + port); err == nil {
							addrMu.Lock()
							if placeholderAddr == nil {
								placeholderAddr = phAddr
							}
							ph = placeholderAddr
							addrMu.Unlock()
						}
					}
				}
				if ph != nil {
					result = append(result, ph)
				}
				return result
			}
			result := []multiaddr.Multiaddr{}
			result = append(result, addrs...)
			result = append(result, obs)
			return result
		}),
	}
	if relayInfo != nil {
		opts = append(opts, libp2p.EnableAutoRelayWithStaticRelays([]peer.AddrInfo{*relayInfo}))
	}

	h, err := libp2p.New(opts...)
	_ = listenMA // avoid unused warning
	if err != nil {
		log.Fatalf("failed to create libp2p host: %v", err)
	}
	defer h.Close()

	if relayInfo != nil {
		reserveOnRelay(baseCtx, h, relayInfo)
	}

	// Get the identify service to access OwnObservedAddrs
	idsHost, hasIDS := any(h).(interface{ IDService() identify.IDService })
	refreshIdentify := func() {
		if !hasIDS || idsHost.IDService() == nil {
			return
		}
		for _, c := range h.Network().Conns() {
			idsHost.IDService().IdentifyConn(c)
		}
	}
	setObserved := func(addr multiaddr.Multiaddr) {
		addrMu.Lock()
		if observedPublicAddr == nil || observedPublicAddr.String() != addr.String() {
			observedPublicAddr = addr
			placeholderAddr = nil
		}
		addrMu.Unlock()
		refreshIdentify()
	}

	// Subscribe to identify events to capture our observed public address
	sub, err := h.EventBus().Subscribe(new(event.EvtPeerIdentificationCompleted))
	if err != nil {
		log.Printf("Warning: could not subscribe to identify events: %v", err)
	} else {
		go func() {
			for evt := range sub.Out() {
				idEvt := evt.(event.EvtPeerIdentificationCompleted)

				// Ignore observed addresses from relayed-only connections
				if isRelayedOnly(h, idEvt.Peer) {
					continue
				}

				// Prefer the observed address from the identify event itself
				if idEvt.ObservedAddr != nil {
					addrStr := idEvt.ObservedAddr.String()
					if relayIP != "" && strings.Contains(addrStr, "/ip4/"+relayIP) {
						continue
					}
					if !strings.Contains(addrStr, "1.2.3.4") && !isPrivateAddr(addrStr) && !isLoopback(addrStr) {
						if observedPublicAddr == nil || observedPublicAddr.String() != addrStr {
							setObserved(idEvt.ObservedAddr)
							fmt.Printf("[OBSERVED] Learned REAL public address %s from peer %s\n", idEvt.ObservedAddr, idEvt.Peer.String()[:12])
						}
					}
				}

				// Fallback: use identify service observed addresses
				if observedPublicAddr == nil && hasIDS && idsHost.IDService() != nil {
					for _, addr := range idsHost.IDService().OwnObservedAddrs() {
						addrStr := addr.String()
						if strings.Contains(addrStr, "1.2.3.4") {
							continue
						}
						if relayIP != "" && strings.Contains(addrStr, "/ip4/"+relayIP) {
							continue
						}
						if !isPrivateAddr(addrStr) && !isLoopback(addrStr) {
							setObserved(addr)
							fmt.Printf("[OBSERVED] Learned REAL public address %s from peer %s\n", addr, idEvt.Peer.String()[:12])
							break
						}
					}
				}
			}
		}()
	}

	fmt.Printf("Local peer ID: %s\n", h.ID())
	fmt.Printf("Listening on: %v\n", h.Addrs())

	// Monitor connection events to see DCUtR upgrades
	h.Network().Notify(&network.NotifyBundle{
		ConnectedF: func(n network.Network, c network.Conn) {
			fmt.Printf("[EVENT] Connected to %s via %s (dir=%s)\n",
				c.RemotePeer().String()[:12], c.RemoteMultiaddr(), c.Stat().Direction)
		},
		DisconnectedF: func(n network.Network, c network.Conn) {
			fmt.Printf("[EVENT] Disconnected from %s via %s\n",
				c.RemotePeer().String()[:12], c.RemoteMultiaddr())
		},
	})

	h.Peerstore().AddAddrs(info.ID, info.Addrs, time.Minute)

	fmt.Printf("\nConnecting to %s...\n", *addrFlag)
	connectCtx, connectCancel := context.WithTimeout(baseCtx, *timeoutFlag)
	defer connectCancel()
	if err := h.Connect(connectCtx, *info); err != nil {
		log.Fatalf("connect failed: %v", err)
	}

	// Show initial connection type
	printConnections(h, info.ID)

	fmt.Println("\nRunning identify...")
	identifyCtx, identifyCancel := context.WithTimeout(baseCtx, *timeoutFlag)
	defer identifyCancel()
	if err := runIdentify(identifyCtx, h, info.ID); err != nil {
		log.Printf("identify failed: %v", err)
	}

	// Periodic ping to observe connection type changes (DCUtR upgrade)
	fmt.Println("\n=== Starting periodic ping (10 times, 2s interval) ===")
	pinger := ping.NewPingService(h)

	for i := 1; i <= 10; i++ {
		pingCtx, pingCancel := context.WithTimeout(baseCtx, *timeoutFlag)
		resCh := pinger.Ping(pingCtx, info.ID)

		// Get current connection type
		connType := getConnectionType(h, info.ID)

		select {
		case res := <-resCh:
			if res.Error != nil {
				fmt.Printf("[%2d/10] %s | PING FAILED: %v\n", i, connType, res.Error)
			} else {
				fmt.Printf("[%2d/10] %s | RTT: %s\n", i, connType, res.RTT)
			}
		case <-pingCtx.Done():
			fmt.Printf("[%2d/10] %s | TIMEOUT\n", i, connType)
		}
		pingCancel()

		if i < 10 {
			time.Sleep(2 * time.Second)
		}
	}

	// Show final connection state
	fmt.Println("\n=== Final connection state ===")
	printConnections(h, info.ID)
}

// printConnections shows all connections to a peer
func printConnections(h host.Host, peerID peer.ID) {
	conns := h.Network().ConnsToPeer(peerID)
	if len(conns) == 0 {
		fmt.Println("  No connections")
		return
	}
	for i, c := range conns {
		connType := "DIRECT"
		addr := c.RemoteMultiaddr().String()
		if contains(addr, "p2p-circuit") {
			connType = "RELAY"
		}
		fmt.Printf("  [%d] %s: %s (dir=%s)\n", i, connType, c.RemoteMultiaddr(), c.Stat().Direction)
	}
}

// getConnectionType returns a summary of current connection type
func getConnectionType(h host.Host, peerID peer.ID) string {
	conns := h.Network().ConnsToPeer(peerID)
	if len(conns) == 0 {
		return "NO_CONN"
	}

	hasRelay := false
	hasDirect := false
	for _, c := range conns {
		addr := c.RemoteMultiaddr().String()
		if contains(addr, "p2p-circuit") {
			hasRelay = true
		} else {
			hasDirect = true
		}
	}

	if hasDirect && hasRelay {
		return "DIRECT+RELAY"
	} else if hasDirect {
		return "DIRECT ✓"
	} else {
		return "RELAY"
	}
}

func contains(s, substr string) bool {
	return len(s) >= len(substr) && (s == substr || len(s) > 0 && containsAt(s, substr))
}

func containsAt(s, substr string) bool {
	for i := 0; i <= len(s)-len(substr); i++ {
		if s[i:i+len(substr)] == substr {
			return true
		}
	}
	return false
}

// isRelayedOnly returns true if all connections to a peer are relayed
func isRelayedOnly(h host.Host, peerID peer.ID) bool {
	conns := h.Network().ConnsToPeer(peerID)
	if len(conns) == 0 {
		return false
	}
	for _, c := range conns {
		if !strings.Contains(c.RemoteMultiaddr().String(), "p2p-circuit") {
			return false
		}
	}
	return true
}

func runIdentify(ctx context.Context, h host.Host, peerID peer.ID) error {
	if h == nil {
		return fmt.Errorf("host is nil")
	}

	idsHost, ok := any(h).(interface{ IDService() identify.IDService })
	if !ok || idsHost.IDService() == nil {
		return fmt.Errorf("identify service not available on host")
	}

	conns := h.Network().ConnsToPeer(peerID)
	if len(conns) == 0 {
		return fmt.Errorf("no active connection to peer")
	}

	done := idsHost.IDService().IdentifyWait(conns[0])
	select {
	case <-done:
	case <-ctx.Done():
		return fmt.Errorf("identify timed out: %w", ctx.Err())
	}

	protocols, err := h.Peerstore().GetProtocols(peerID)
	if err != nil {
		return fmt.Errorf("get protocols: %w", err)
	}
	if len(protocols) == 0 {
		fmt.Println("identify protocols: (none)")
		return nil
	}
	fmt.Println("identify protocols:")
	for _, p := range protocols {
		fmt.Printf("  %s\n", p)
	}
	return nil
}
