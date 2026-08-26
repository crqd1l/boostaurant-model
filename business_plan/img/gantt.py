"""Диаграмма Ганта первого года работы предприятия.

Строит рисунок к разделу 5 «Организационный план»: этапы запуска продаж,
найма и разработки по месяцам первого года.

Запуск: .venv/bin/python business_plan/img/gantt.py
"""

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Patch

OUT = Path(__file__).parent / "gantt.png"

ACCENT = "#8C4A2F"      # работы основателя
SECOND = "#B08968"      # наём и работы персонала
MUTED = "#C8B9AE"       # фоновые процессы
TEXT = "#2B2724"

# (название, месяц начала (1..12), длительность, цвет)
TASKS = [
    ("Подготовка материалов продаж, список заведений", 1, 1, ACCENT),
    ("Набор пилотных ресторанов (5 заведений)", 1, 3, ACCENT),
    ("Подключение и сопровождение пилота", 2, 2, ACCENT),
    ("Измерение результата, оформление кейсов", 3, 2, ACCENT),
    ("Перевод пилотных заведений на подписку", 4, 1, ACCENT),
    ("Наём специалиста по рекомендательным системам", 4, 1, SECOND),
    ("Наём и обучение специалиста по внедрению", 5, 2, SECOND),
    ("Регулярные продажи (контакты, встречи)", 4, 9, MUTED),
    ("Подключение заказчиков", 4, 9, MUTED),
    ("Разработка интеграции с r_keeper", 7, 4, SECOND),
    ("Подготовка заявки на грант «Старт-1»", 10, 3, ACCENT),
]

plt.rcParams.update(
    {
        "font.family": "Times New Roman",
        "font.size": 10.5,
        "text.color": TEXT,
        "axes.labelcolor": TEXT,
        "xtick.color": TEXT,
        "ytick.color": TEXT,
    }
)

fig, ax = plt.subplots(figsize=(11, 5.0))

for i, (name, start, dur, color) in enumerate(TASKS):
    ax.barh(i, dur, left=start - 0.5, height=0.55, color=color)

ax.set_yticks(range(len(TASKS)), [t[0] for t in TASKS])
ax.invert_yaxis()
ax.set_xlim(0.5, 12.5)
ax.set_xticks(range(1, 13), [str(m) for m in range(1, 13)])
ax.set_xlabel("Месяц первого года")

# веха: переход пилота на оплату
ax.axvline(3.5, color=TEXT, linewidth=1.1, linestyle="--", zorder=3)
ax.text(3.6, len(TASKS) - 0.3, "начало поступлений", fontsize=9.5, style="italic")

ax.spines[["top", "right", "left"]].set_visible(False)
ax.spines["bottom"].set_color("#B5ABA3")
ax.tick_params(axis="y", length=0)
ax.grid(axis="x", color="#E5DED8", linewidth=0.8)
ax.set_axisbelow(True)

ax.legend(
    handles=[
        Patch(color=ACCENT, label="основатель"),
        Patch(color=SECOND, label="наёмный персонал"),
        Patch(color=MUTED, label="регулярная деятельность"),
    ],
    loc="upper center",
    bbox_to_anchor=(0.5, -0.13),
    ncol=3,
    frameon=False,
    fontsize=9.5,
)

fig.tight_layout(pad=1.1)
fig.savefig(OUT, dpi=200, facecolor="white")
print(f"сохранено: {OUT}")
