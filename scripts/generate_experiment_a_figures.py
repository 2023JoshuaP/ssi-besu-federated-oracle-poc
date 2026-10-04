"""Generate publication-ready figures from experiment-A JSON outputs.

No live system is required. Figures are based only on the preserved results.
"""
import json
import statistics
from collections import defaultdict
from pathlib import Path

import matplotlib.pyplot as plt

ROOT_DIR = Path(__file__).resolve().parents[1]
RESULTS_DIR = ROOT_DIR / "results"
FIGURES_DIR = RESULTS_DIR / "figures"
LEVELS = ["A-P1", "A-P2", "A-P3", "A-P4"]
CONCURRENCY = [1, 10, 25, 50]
COLORS = {"baseline": "#0072B2", "quorum": "#D55E00"}


def load_json(path):
    return json.loads(path.read_text())


def performance_data():
    data = {"baseline": {}, "quorum": {}}
    templates = {
        "baseline": "experiment_a_baseline1of1_{}_summary.json",
        "quorum": "experiment_a_quorum3of5_{}_summary.json",
    }
    for alternative, template in templates.items():
        for level in LEVELS:
            blocks = load_json(RESULTS_DIR / template.format(level))
            data[alternative][level] = {
                "throughput": statistics.mean(block["throughput_rps"] for block in blocks),
                "gas": statistics.mean(block["gas_mean"] for block in blocks),
                "p50": statistics.mean(block["latency_ms"]["p50"] for block in blocks) / 1000,
                "p95": statistics.mean(block["latency_ms"]["p95"] for block in blocks) / 1000,
                "p99": statistics.mean(block["latency_ms"]["p99"] for block in blocks) / 1000,
                "success": sum(block["successful"] for block in blocks),
                "errors": sum(block["errors"] for block in blocks),
            }
    return data


def functional_data():
    rows = [json.loads(line) for line in (RESULTS_DIR / "experiment_a_functional_analysis_120.jsonl").read_text().splitlines() if line]
    grouped = defaultdict(list)
    for row in rows:
        grouped[row["validity_case"]].append(row)
    result = []
    for number in range(1, 13):
        case = f"A{number}"
        runs = grouped[case]
        result.append({
            "case": case,
            "passed": sum(run["passed"] for run in runs),
            "expected": runs[0]["expected_result"],
        })
    return result


def save(figure, name):
    FIGURES_DIR.mkdir(parents=True, exist_ok=True)
    figure.tight_layout()
    figure.savefig(FIGURES_DIR / name, dpi=300, bbox_inches="tight")
    plt.close(figure)


def plot_functional(rows):
    figure, axis = plt.subplots(figsize=(10, 4.6))
    labels = [row["case"] for row in rows]
    values = [row["passed"] for row in rows]
    colors = ["#009E73" if row["expected"] == "accepted" else "#CC79A7" for row in rows]
    bars = axis.bar(labels, values, color=colors)
    axis.set_ylim(0, 10.8)
    axis.set_ylabel("Ejecuciones que coincidieron con el resultado esperado")
    axis.set_xlabel("Caso funcional")
    axis.set_title("Correctitud funcional del quorum Django 3-de-5 (n = 10 por caso)")
    axis.set_yticks(range(0, 11, 2))
    for bar, row in zip(bars, rows):
        axis.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + .15, f"{row['passed']}/10", ha="center", fontsize=9)
    axis.text(0.01, -0.22, "Verde: reserva esperada aceptada. Magenta: reserva esperada rechazada.", transform=axis.transAxes, fontsize=8)
    save(figure, "figure_a_functional_correctness.png")


def plot_throughput(data):
    figure, axis = plt.subplots(figsize=(8.5, 4.8))
    x = list(range(len(LEVELS)))
    width = .36
    for offset, alternative, label in [(-width / 2, "baseline", "Trusted Verifier 1-de-1"), (width / 2, "quorum", "Django quorum 3-de-5")]:
        values = [data[alternative][level]["throughput"] for level in LEVELS]
        axis.bar([item + offset for item in x], values, width, label=label, color=COLORS[alternative])
    axis.set_xticks(x, [f"{level}\n(c={concurrency})" for level, concurrency in zip(LEVELS, CONCURRENCY)])
    axis.set_ylabel("Reservas aceptadas por segundo")
    axis.set_title("Throughput end-to-end por nivel de concurrencia")
    axis.legend()
    axis.grid(axis="y", alpha=.25)
    save(figure, "figure_a_throughput_comparison.png")


def plot_latency(data):
    figure, axes = plt.subplots(1, 2, figsize=(11, 4.5), sharex=True)
    for alternative, label in [("baseline", "Trusted Verifier 1-de-1"), ("quorum", "Django quorum 3-de-5")]:
        color = COLORS[alternative]
        axes[0].plot(CONCURRENCY, [data[alternative][level]["p50"] for level in LEVELS], marker="o", color=color, label=label)
        axes[1].plot(CONCURRENCY, [data[alternative][level]["p95"] for level in LEVELS], marker="o", linestyle="-", color=color, label=f"{label} p95")
        axes[1].plot(CONCURRENCY, [data[alternative][level]["p99"] for level in LEVELS], marker="s", linestyle="--", color=color, label=f"{label} p99")
    axes[0].set_title("Latencia p50")
    axes[1].set_title("Latencias de cola")
    for axis in axes:
        axis.set_xlabel("Solicitudes simultáneas")
        axis.set_ylabel("Segundos")
        axis.set_xticks(CONCURRENCY)
        axis.grid(alpha=.25)
        axis.legend(fontsize=7)
    figure.suptitle("Latencia end-to-end: baseline frente a quorum 3-de-5", y=1.03)
    save(figure, "figure_a_latency_comparison.png")


def plot_gas(data):
    figure, axis = plt.subplots(figsize=(8.5, 4.8))
    x = list(range(len(LEVELS)))
    width = .36
    baseline = [data["baseline"][level]["gas"] for level in LEVELS]
    quorum = [data["quorum"][level]["gas"] for level in LEVELS]
    axis.bar([item - width / 2 for item in x], baseline, width, label="Trusted Verifier 1-de-1", color=COLORS["baseline"])
    axis.bar([item + width / 2 for item in x], quorum, width, label="Django quorum 3-de-5", color=COLORS["quorum"])
    axis.set_xticks(x, LEVELS)
    axis.set_ylabel("Gas medio por reserva")
    axis.set_title("Coste on-chain de la autorización de reserva")
    axis.legend()
    axis.grid(axis="y", alpha=.25)
    increase = (statistics.mean(quorum) / statistics.mean(baseline) - 1) * 100
    axis.text(.5, .04, f"Sobrecoste medio del quorum: {increase:.1f}%", transform=axis.transAxes, ha="center", fontsize=9)
    save(figure, "figure_a_gas_comparison.png")


def write_table(data):
    lines = ["alternative,level,concurrency,throughput_rps,p50_seconds,p95_seconds,p99_seconds,gas_mean,successful,errors"]
    for alternative, label in [("baseline", "trusted_verifier_1of1"), ("quorum", "django_quorum_3of5")]:
        for level, concurrency in zip(LEVELS, CONCURRENCY):
            row = data[alternative][level]
            lines.append(
                f"{label},{level},{concurrency},{row['throughput']:.6f},{row['p50']:.6f},{row['p95']:.6f},{row['p99']:.6f},{row['gas']:.3f},{row['success']},{row['errors']}"
            )
    (FIGURES_DIR / "experiment_a_performance_aggregated.csv").write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    performance = performance_data()
    functional = functional_data()
    plot_functional(functional)
    plot_throughput(performance)
    plot_latency(performance)
    plot_gas(performance)
    write_table(performance)
    print(f"Figuras y tabla creadas en {FIGURES_DIR}")
