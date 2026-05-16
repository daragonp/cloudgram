"""Helpers de formateo para mensajes de Telegram.

Estilo "Apple-clean":
  • Separador horizontal corto (12 chars) que NO se rompe en móvil.
  • Tipografía cursiva para descripciones.
  • Sin emojis numéricos (1️⃣ 2️⃣ …); se usan dígitos simples 1., 2.
  • Iconos sutiles: 📄 nombre, 💡 IA, 🏷 tags, 🔗 link.
  • Encabezados con línea de contexto en _cursiva_.

Todas las funciones devuelven texto Markdown (legacy de Telegram).
"""
from typing import Optional

# Separador horizontal corto: 12 caracteres siempre caben en una línea de móvil.
RULE = "━━━━━━━━━━━━"


def md_escape(s: Optional[str]) -> str:
    """Escapa caracteres conflictivos de Markdown legacy para que no rompan el
    parseo. No es perfecto (Telegram tiene limitaciones), pero evita los más
    comunes en nombres de archivo y summaries."""
    if not s:
        return ""
    return (str(s)
            .replace("*", "·")
            .replace("_", "‿")
            .replace("`", "ʼ")
            .replace("[", "(")
            .replace("]", ")"))


def header(title: str, subtitle: Optional[str] = None) -> str:
    """Encabezado de panel. Devuelve dos líneas (título + cursiva)."""
    lines = [f"*{title}*"]
    if subtitle:
        lines.append(f"_{subtitle}_")
    return "\n".join(lines)


def score_emoji(score_pct: int) -> str:
    if score_pct >= 80: return "🔥"
    if score_pct >= 60: return "⭐"
    if score_pct >= 40: return "✓"
    return "·"


def result_card(
    idx: int,
    name: str,
    score_pct: Optional[int] = None,
    summary: Optional[str] = None,
    llm_reason: Optional[str] = None,
    tags: Optional[str] = None,
    url: Optional[str] = None,
    service: Optional[str] = None,
    summary_max: int = 180,
) -> str:
    """Bloque de UN resultado/elemento. Empieza con RULE y termina con saltos."""
    parts = [RULE]

    name_safe = md_escape(name)
    if score_pct is not None:
        emoji = score_emoji(score_pct)
        parts.append(f"*{idx}.* `{name_safe}`")
        parts.append(f"{emoji} *{score_pct}% relevancia*")
    else:
        parts.append(f"*{idx}.* `{name_safe}`")
        if service:
            parts.append(f"_{service.upper()}_")

    if summary:
        s = summary.strip()
        if len(s) > summary_max:
            s = s[:summary_max].rstrip() + "…"
        parts.append("")
        parts.append(f"_{md_escape(s)}_")

    if llm_reason:
        reason_safe = md_escape(llm_reason)[:120]
        parts.append(f"\n💡 _Evaluado por IA: {reason_safe}_")

    if tags:
        tag_safe = md_escape(str(tags))[:80]
        parts.append(f"🏷  _{tag_safe}_")

    if url:
        parts.append(f"🔗 [Abrir en la nube]({url})")

    return "\n".join(parts)


def render_list(title: str, subtitle: Optional[str], cards: list) -> str:
    """Combina header + cards + cierre de RULE."""
    blocks = [header(title, subtitle), ""] + cards + [RULE]
    return "\n".join(blocks)


def progress_bar(value: float, total: float, width: int = 10) -> str:
    """Barra `■■■■■□□□□□` para mostrar progreso visual."""
    if total <= 0:
        return "□" * width
    pct = max(0.0, min(1.0, value / total))
    filled = int(round(pct * width))
    return "■" * filled + "□" * (width - filled)


def kv_row(label: str, value: str, mono: bool = True) -> str:
    """Una fila tipo `Label   value` para paneles de stats/info."""
    val = f"`{value}`" if mono else value
    return f"• *{label}*  {val}"


def status_dot(ok: bool) -> str:
    """Punto verde/rojo para online/offline."""
    return "🟢" if ok else "🔴"
