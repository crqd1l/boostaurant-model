"""Матрица рисков проекта.

Строит рисунок к разделу 9 «Анализ рисков»: размещение рисков в координатах
«вероятность — влияние». Номера соответствуют таблице рисков раздела.

Запуск: .venv/bin/python business_plan/img/risks.py
"""

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Patch

OUT = Path(__file__).parent / "risks.png"

ACCENT = "#8C4A2F"      # критические — требуют мероприятий
SECOND = "#B08968"      # существенные — под наблюдением
MUTED = "#C8B9AE"       # умеренные — принимаются
TEXT = "#2B2724"

# Зоны матрицы: (вероятность + влияние) задаёт уровень риска.
ZONE_LOW = "#F2EDE9"
ZONE_MID = "#E6D9CF"
ZONE_HIGH = "#D6BFAE"

# (номер, вероятность 1..3, влияние 1..3, подпись смещения)
# 1 — низкая/низкое, 2 — средняя/среднее, 3 — высокая/высокое
RISKS = [
    (1, 3, 3),   # сжатие льготы по НДС у заказчиков
    (2, 3, 2),   # сокращение числа заведений
    (3, 2, 3),   # рекомендации у платформ лояльности
    (4, 2, 3),   # изменение политики iiko
    (5, 2, 2),   # зависимость от единственной системы учёта
    (6, 1, 3),   # отказ сервиса, потеря данных
    (7, 1, 3),   # нарушение порядка обработки ПДн
    (8, 2, 2),   # утрата права на АУСН
    (9, 3, 3),   # недостижение плана подключений
    (10, 2, 3),  # отток заказчиков
    (11, 2, 3),  # уход ключевого работника
]

# Смещения точек внутри клетки, чтобы совпадающие риски не накладывались.
# В клетке «средняя/высокое» четыре риска (3, 4, 10, 11) — размещаются углом.
OFFSETS = {
    1: (-0.14, 0.0), 9: (0.14, 0.0),          # высокая / высокое
    3: (-0.16, 0.10), 4: (0.16, 0.10),        # средняя / высокое
    10: (-0.16, -0.10), 11: (0.16, -0.10),
    5: (-0.14, 0.0), 8: (0.14, 0.0),          # средняя / среднее
    6: (-0.14, 0.0), 7: (0.14, 0.0),          # низкая / высокое
}

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

fig, ax = plt.subplots(figsize=(8.2, 5.0))

# Фоновые зоны: уровень определяется суммой координат. Уровень влияния «низкое»
# не выводится — рисков этого уровня в перечне нет, пустая полоса лишь занимала бы
# треть рисунка.
for p in (1, 2, 3):
    for v in (2, 3):
        total = p + v
        if total <= 3:
            color = ZONE_LOW
        elif total <= 5:
            color = ZONE_MID
        else:
            color = ZONE_HIGH
        ax.add_patch(
            plt.Rectangle(
                (p - 0.5, v - 0.5), 1, 1,
                facecolor=color, edgecolor="white", linewidth=1.6, zorder=0,
            )
        )


def level_color(p: int, v: int) -> str:
    """Цвет точки: критические риски — высокое влияние при вероятности выше низкой."""
    if v == 3 and p >= 2:
        return ACCENT
    if p + v >= 4:
        return SECOND
    return MUTED


for num, p, v in RISKS:
    dx, dy = OFFSETS.get(num, (0.0, 0.0))
    ax.scatter(
        p + dx, v + dy, s=310, color=level_color(p, v),
        zorder=3, edgecolors="white", linewidths=1.4,
    )
    ax.text(
        p + dx, v + dy, str(num), ha="center", va="center",
        color="white", fontsize=10.5, fontweight="bold", zorder=4,
    )

ax.set_xlim(0.5, 3.5)
ax.set_ylim(1.5, 3.5)
ax.set_xticks([1, 2, 3], ["низкая", "средняя", "высокая"])
ax.set_yticks([2, 3], ["среднее", "высокое"])
ax.set_xlabel("Вероятность")
ax.set_ylabel("Влияние на проект")

ax.spines[["top", "right"]].set_visible(False)
ax.spines[["bottom", "left"]].set_color("#B5ABA3")
ax.tick_params(length=0)
ax.set_axisbelow(True)

ax.legend(
    handles=[
        Patch(color=ACCENT, label="критические — требуют мероприятий"),
        Patch(color=SECOND, label="существенные — под наблюдением"),
        Patch(color=MUTED, label="умеренные — принимаются"),
    ],
    loc="upper center",
    bbox_to_anchor=(0.5, -0.11),
    ncol=1,
    frameon=False,
    fontsize=9.5,
)

fig.tight_layout(pad=1.1)
fig.savefig(OUT, dpi=200, facecolor="white")
print(f"сохранено: {OUT}")
