"""Диаграмма сегментов общепита РФ по итогам 2025 года.

Строит рисунок к разделу 3 «Описание отрасли»: число заведений по форматам
и темп прироста за год. Данные — INFOLine (см. источник [1] раздела 3).

Запуск: .venv/bin/python business_plan/img/segments.py
"""

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

OUT = Path(__file__).parent / "segments.png"

# Формат, число заведений (тыс.), прирост за 2025 год (%)
DATA = [
    ("Быстрое питание", 95, 9.0),
    ("Рестораны\nполного обслуживания", 68, 6.5),
    ("Кофейни и grab & go", 59, 8.4),
    ("Бары", 20, 7.0),
]

# Целевой сегмент выделен насыщенным тоном, остальные — приглушённым
ACCENT = "#8C4A2F"
MUTED = "#C8B9AE"
TEXT = "#2B2724"

plt.rcParams.update(
    {
        "font.family": "Times New Roman",
        "font.size": 11,
        "text.color": TEXT,
        "axes.labelcolor": TEXT,
        "xtick.color": TEXT,
        "ytick.color": TEXT,
    }
)

fig, (ax_n, ax_g) = plt.subplots(
    1, 2, figsize=(10, 4.2), gridspec_kw={"width_ratios": [1.35, 1]}
)

labels = [d[0] for d in DATA]
counts = [d[1] for d in DATA]
growth = [d[2] for d in DATA]
colors = [ACCENT if "полного" in lbl else MUTED for lbl in labels]
ypos = range(len(DATA))

# Левая панель — размер сегмента
ax_n.barh(ypos, counts, color=colors, height=0.62)
ax_n.set_yticks(list(ypos), labels)
ax_n.invert_yaxis()
ax_n.set_xlabel("Число заведений, тыс.")
ax_n.set_xlim(0, max(counts) * 1.18)
for y, v in zip(ypos, counts):
    ax_n.text(v + 1.5, y, f"{v}", va="center", fontsize=10.5)

# Правая панель — темп прироста
ax_g.barh(ypos, growth, color=colors, height=0.62)
ax_g.set_yticks(list(ypos), [])
ax_g.invert_yaxis()
ax_g.set_xlabel("Прирост за 2025 год, %")
ax_g.set_xlim(0, max(growth) * 1.22)
for y, v in zip(ypos, growth):
    ax_g.text(v + 0.15, y, f"{v:g} %".replace(".", ","), va="center", fontsize=10.5)

for ax in (ax_n, ax_g):
    ax.spines[["top", "right", "left"]].set_visible(False)
    ax.spines["bottom"].set_color("#B5ABA3")
    ax.tick_params(axis="y", length=0)
    ax.grid(axis="x", color="#E5DED8", linewidth=0.8)
    ax.set_axisbelow(True)

fig.tight_layout(pad=1.2)
fig.savefig(OUT, dpi=200, facecolor="white")
print(f"сохранено: {OUT}")
