function render(ctx, gfx) {
  var buf = ctx.globalBuffer;
  var csv = buf && buf.csv;

  if (!csv || csv.length === 0) {
    gfx.drawRect({ x: 0, y: 0, w: ctx.width, h: ctx.height, fill: "#111111" });
    gfx.drawText({
      x: ctx.width / 2, y: ctx.height / 2,
      text: "No sensor CSV data in buffer",
      size: 14, color: "#666666", align: "center",
    });
    return;
  }

  // ── CSV columns (decoded by mt_detector.lua): ────────────────────────────────
  //   0:ax_g  1:ay_g  2:az_g  3:vx_mm_s  4:vy_mm_s  5:vz_mm_s  6:temp_c

  var GROUPS = [
    {
      label: "Acceleration (g)",
      min: -0.5, max: 0.5,
      channels: [
        { label: "ax", col: 0, color: "#ef4444" },
        { label: "ay", col: 1, color: "#3b82f6" },
        { label: "az", col: 2, color: "#22c55e" },
      ],
    },
    {
      label: "Velocity (mm/s)",
      min: 0, max: 5,
      channels: [
        { label: "vx", col: 3, color: "#f97316" },
        { label: "vy", col: 4, color: "#a855f7" },
        { label: "vz", col: 5, color: "#06b6d4" },
      ],
    },
    {
      label: "Temperature (°C)",
      min: 0, max: 60,
      channels: [
        { label: "temp", col: 6, color: "#facc15" },
      ],
    },
  ];

  // ── Layout ────────────────────────────────────────────────────────────────────
  var PAD_X = 6, PAD_Y = 6, GAP = 4;
  var chartW = ctx.width  - PAD_X * 2;
  var chartH = Math.floor((ctx.height - PAD_Y * 2 - GAP * (GROUPS.length - 1)) / GROUPS.length);

  function buildSeries(ch) {
    var out = [];
    for (var i = 0; i < csv.length; i++) {
      var row = csv[i];
      if (row && row.length > ch.col) {
        var v = parseFloat(row[ch.col]);
        if (!isNaN(v)) { out.push({ t: i, v: v }); }
      }
    }
    return out;
  }

  for (var gi = 0; gi < GROUPS.length; gi++) {
    var grp = GROUPS[gi];
    var gx  = PAD_X;
    var gy  = PAD_Y + gi * (chartH + GAP);

    gfx.drawRect({ x: gx, y: gy, w: chartW, h: chartH, fill: "rgba(0,0,0,0.55)", radius: 3 });

    var allSeries = grp.channels.map(function(ch) { return buildSeries(ch); });

    for (var ci = 0; ci < grp.channels.length; ci++) {
      gfx.drawLineChart({
        x: gx, y: gy, w: chartW, h: chartH,
        series: allSeries[ci],
        min: grp.min, max: grp.max,
        stroke: grp.channels[ci].color,
        strokeWidth: 1.5,
      });
    }

    // Group label
    gfx.drawText({ x: gx + 4, y: gy + 11, text: grp.label, size: 10, color: "rgba(255,255,255,0.5)" });

    // Y-axis range hints
    gfx.drawText({ x: gx + 4, y: gy + 22,         text: grp.max.toFixed(2), size: 8, color: "rgba(255,255,255,0.35)" });
    gfx.drawText({ x: gx + 4, y: gy + chartH - 4, text: grp.min.toFixed(2), size: 8, color: "rgba(255,255,255,0.35)" });

    // Legend (right side, bottom of chart)
    var legendX = gx + chartW - 4;
    for (var li = grp.channels.length - 1; li >= 0; li--) {
      var ch = grp.channels[li];
      var lw = gfx.measureText({ text: ch.label, size: 9 }).w;
      legendX -= lw;
      gfx.drawText({ x: legendX, y: gy + chartH - 4, text: ch.label, size: 9, color: ch.color });
      legendX -= 14;
      gfx.drawRect({ x: legendX, y: gy + chartH - 9, w: 10, h: 2, fill: ch.color });
      legendX -= 6;
    }
  }

  // Footer: sample count + timestamp
  gfx.drawText({
    x: ctx.width - PAD_X, y: ctx.height - 2,
    text: csv.length + " samples  \\u00b7  " + ctx.time.utc,
    size: 9, color: "rgba(255,255,255,0.25)", align: "right",
  });
}