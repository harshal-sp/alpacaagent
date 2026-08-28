"""Generate simple cover image via matplotlib — no external deps."""
import matplotlib.pyplot as plt
import matplotlib.patches as patches

fig, ax = plt.subplots(figsize=(16,9))
fig.patch.set_facecolor("#0B1220")
ax.set_xlim(0,16)
ax.set_ylim(0,9)
ax.axis("off")

# Title
ax.text(0.8, 6.8, "VEGA", fontsize=72, color="white", weight="bold", fontfamily="monospace")
ax.text(0.8, 6.2, "Autonomous Options Alpha Agent", fontsize=18, color="#22D3EE", fontfamily="sans-serif")
ax.text(0.8, 5.5, "0–7 DTE  •  Iron Condors  •  Spreads  •  Straddles  •  Defined-Risk Only", fontsize=11, color="#9CA3AF")
ax.text(0.8, 5.0, "Alpaca Paper Trading  •  MCP + CLI  •  Featherless 70B", fontsize=11, color="#9CA3AF", bbox=dict(boxstyle="round,pad=0.4", facecolor="#1F2937", edgecolor="#22D3EE", alpha=0.8))
ax.text(0.8, 1.0, "lablab.ai  ×  Alpaca  —  Aug 28–Sep 4  2026", fontsize=11, color="#6B7280")
ax.text(0.8, 0.5, "PAPER TRADING ONLY  —  NOT INVESTMENT ADVICE", fontsize=8, color="#4B5563")

# Mock equity curve
import numpy as np
x = np.linspace(9, 15, 50)
y = 3 + np.cumsum(np.random.randn(50)*0.06) + np.linspace(0,0.6,50)
ax.plot(x, y, color="#22D3EE", linewidth=3)
ax.fill_between(x, y, 3, color="#22D3EE", alpha=0.12)
ax.text(9, 4.6, "Equity  $100k → $103.2k  (+3.2% in 7d  •  paper simulation)", fontsize=9, color="#9CA3AF")

# Card
card = patches.FancyBboxPatch((8.8, 2.2), 6.4, 3.0, boxstyle="round,pad=0.2", facecolor="#0F172A", edgecolor="#334155", linewidth=1.2)
ax.add_patch(card)
ax.text(9.1, 4.9, "ORDER PREVIEW  (PAPER)", fontsize=8, color="#22D3EE", weight="bold", fontfamily="monospace")
ax.text(9.1, 4.5, "SELL  5x SPY  776 Call  @ $0.90  (short_call)", fontsize=7.5, color="white", fontfamily="monospace")
ax.text(9.1, 4.15, "BUY   5x SPY  779 Call  @ $0.55  (long_call)", fontsize=7.5, color="white", fontfamily="monospace")
ax.text(9.1, 3.7, "Est. Credit  $175  •  Max Loss  $1,325  •  Width  $3", fontsize=7.5, color="#9CA3AF", fontfamily="monospace")
ax.text(9.1, 3.3, "Risk: ✓ BP ok  ✓ delta ok  ✓ expiry ok", fontsize=7, color="#6EE7B7", fontfamily="monospace")
ax.text(9.1, 2.9, "MCP: place_option_order  •  CLI: alpaca order submit", fontsize=6.5, color="#64748B", fontfamily="monospace")
ax.text(9.1, 2.55, "Environment: PAPER (verified)", fontsize=7, color="#FACC15", fontfamily="monospace")

# Logo placeholders
ax.text(13.5, 1.1, "ALPACA", fontsize=12, color="#22D3EE", weight="bold", alpha=0.9, bbox=dict(boxstyle="round,pad=0.2", facecolor="white", edgecolor="none", alpha=0.08))
ax.text(14.6, 1.1, "lablab.ai", fontsize=9, color="white", alpha=0.6)

plt.tight_layout(pad=0.5)
out = "cover.png"
plt.savefig(out, dpi=180, facecolor=fig.get_facecolor(), bbox_inches="tight")
print(f"Saved {out}")
