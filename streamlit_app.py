from __future__ import annotations

import io
import tempfile
from contextlib import redirect_stdout
from copy import deepcopy
from datetime import datetime
from pathlib import Path
import math
import re

import altair as alt
import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

from moteur_ordonnancement import PARAMS, export_xl, run


REQUIRED_FILES = [
    "articles.csv",
    "stock.csv",
    "commandes_clients.csv",
    "of_entetes.csv",
    "of_composants.csv",
    "receptions_oa.csv",
    "gammes.csv",
]


def build_params(
    data_dir: str,
    output_dir: str,
    horizon_jours: int,
    jours_ouvres: int,
    capacite_heures_semaine: int,
    allocation_sequentielle: bool,
    deduire_allocations: bool,
    perimetre_chargement: str,
    mode_injection: str,
    separateur_csv: str,
    encoding: str,
    date_reference: datetime,
) -> dict:
    params = deepcopy(PARAMS)
    params.update(
        {
            "dossier_data": ensure_trailing_slash(data_dir),
            "dossier_output": ensure_trailing_slash(output_dir),
            "horizon_jours": horizon_jours,
            "jours_ouvres_avant_expedition": jours_ouvres,
            "capacite_heures_semaine": capacite_heures_semaine,
            "allocation_sequentielle": allocation_sequentielle,
            "deduire_allocations": deduire_allocations,
            "perimetre_chargement": perimetre_chargement,
            "mode_injection": mode_injection,
            "separateur_csv": separateur_csv,
            "encoding": encoding,
            "date_reference": date_reference.replace(hour=0, minute=0, second=0, microsecond=0),
        }
    )
    return params


def ensure_trailing_slash(path_value: str) -> str:
    return path_value if path_value.endswith("/") else f"{path_value}/"


def list_data_files(data_dir: str) -> tuple[list[str], list[str]]:
    root = Path(data_dir)
    present: list[str] = []
    missing: list[str] = []
    for filename in REQUIRED_FILES:
        if (root / filename).exists():
            present.append(filename)
        else:
            missing.append(filename)
    return present, missing


def run_engine(params: dict) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, str]:
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        df_cmd, df_det, df_plan, df_crit, df_charge_segments = run(params)
    return df_cmd, df_det, df_plan, df_crit, df_charge_segments, buffer.getvalue()


def build_excel_bytes(
    df_cmd: pd.DataFrame,
    df_det: pd.DataFrame,
    df_plan: pd.DataFrame,
    df_crit: pd.DataFrame,
    params: dict,
) -> bytes:
    export_params = deepcopy(params)
    with tempfile.TemporaryDirectory() as tmpdir:
        export_params["dossier_output"] = ensure_trailing_slash(tmpdir)
        export_path = export_xl(df_cmd.copy(), df_det.copy(), df_plan.copy(), df_crit.copy(), export_params)
        return Path(export_path).read_bytes()


def summarize_commandes(df_cmd: pd.DataFrame) -> dict[str, int]:
    if df_cmd.empty:
        return {"VERT": 0, "ORANGE": 0, "ROUGE": 0, "sous_ensembles": 0}
    return {
        "VERT": int((df_cmd.get("Feu Ligne") == "VERT").sum()),
        "ORANGE": int((df_cmd.get("Feu Ligne") == "ORANGE").sum()),
        "ROUGE": int((df_cmd.get("Feu Ligne") == "ROUGE").sum()),
        "sous_ensembles": int((df_cmd.get("Type Flux") == "sous-ensemble").sum()),
    }


def parse_week_bounds(week_label: str) -> tuple[datetime, datetime]:
    match = re.fullmatch(r"S(\d{2})-(\d{4})", str(week_label).strip())
    if match is None:
        raise ValueError(f"Format semaine invalide: {week_label}")
    week_number = int(match.group(1))
    year = int(match.group(2))
    week_start = datetime.fromisocalendar(year, week_number, 1)
    week_end = datetime.fromisocalendar(year, week_number, 7).replace(hour=23, minute=59, second=59)
    return week_start, week_end


def format_dates_for_display(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    display_df = df.copy()
    for column in display_df.columns:
        lowered = str(column).lower()
        if "date" in lowered:
            converted = pd.to_datetime(display_df[column], errors="coerce")
            display_df[column] = converted.dt.strftime("%d/%m/%Y").fillna("")
    return display_df


def build_plan_charge_matrix(df_plan: pd.DataFrame, metric_suffix: str) -> pd.DataFrame:
    if df_plan.empty:
        return df_plan
    value_columns = [column for column in df_plan.columns if column.endswith(metric_suffix)]
    if not value_columns:
        return pd.DataFrame()

    melted = df_plan[["Semaine", *value_columns]].melt(
        id_vars=["Semaine"],
        value_vars=value_columns,
        var_name="Poste",
        value_name="Valeur",
    )
    melted["Poste"] = melted["Poste"].str.replace(metric_suffix, "", regex=False)
    pivot = melted.pivot(index="Poste", columns="Semaine", values="Valeur")
    pivot = pivot.sort_index()
    return pivot.round(0)


def load_poste_labels(params: dict) -> dict[str, str]:
    gammes_path = Path(params["dossier_data"]) / "gammes.csv"
    if not gammes_path.exists():
        return {}

    gammes = pd.read_csv(
        gammes_path,
        sep=params["separateur_csv"],
        encoding=params["encoding"],
        skipinitialspace=True,
    )
    gammes = gammes.loc[:, ~gammes.columns.str.startswith("Unnamed")]
    if len(gammes.columns) != 4:
        return {}

    gammes.columns = ["itmref", "poste_charge", "libelle_poste", "cadence"]
    gammes["poste_charge"] = gammes["poste_charge"].astype(str).str.strip()
    gammes["libelle_poste"] = gammes["libelle_poste"].astype(str).str.strip()
    label_map: dict[str, str] = {}

    for poste, rows in gammes.groupby("poste_charge"):
        labels = [label for label in rows["libelle_poste"].dropna().tolist() if label and label.lower() != "nan"]
        if labels:
            label_map[str(poste)] = labels[0]

    return label_map


def label_plan_charge_index(df: pd.DataFrame, label_map: dict[str, str]) -> pd.DataFrame:
    if df.empty:
        return df
    labeled = df.copy()
    labeled.index = [
        f"{poste} - {label_map[poste]}" if poste in label_map else poste
        for poste in labeled.index
    ]
    return labeled


def style_plan_charge_matrix(df: pd.DataFrame, capacity_by_week: dict[str, float], metric_mode: str):
    def style_frame(frame: pd.DataFrame):
        styled = pd.DataFrame("", index=frame.index, columns=frame.columns)
        for column_name in frame.columns:
            capacity = float(capacity_by_week.get(str(column_name), 0.0) or 0.0)
            for row_name in frame.index:
                value = frame.at[row_name, column_name]
                if pd.isna(value):
                    continue
                try:
                    numeric_value = float(value)
                except (TypeError, ValueError):
                    continue

                ratio = numeric_value if metric_mode != "Heures" else ((numeric_value / capacity * 100.0) if capacity > 0 else 0.0)
                if ratio > 100:
                    styled.at[row_name, column_name] = "background-color: #fecaca; color: #991b1b;"
                elif ratio >= 80:
                    styled.at[row_name, column_name] = "background-color: #fde68a; color: #92400e;"
                elif ratio > 0:
                    styled.at[row_name, column_name] = "background-color: #dcfce7; color: #166534;"
        return styled

    return df.style.apply(style_frame, axis=None).format("{:.1f}")


def style_commandes_table(df: pd.DataFrame):
    if df.empty or "Feu Ligne" not in df.columns:
        return df

    color_map = {
        "VERT": "background-color: #dcfce7; color: #166534;",
        "ORANGE": "background-color: #fde68a; color: #92400e;",
        "ROUGE": "background-color: #fecaca; color: #991b1b;",
    }

    def style_row(row: pd.Series) -> pd.Series:
        feu = row.get("Feu Ligne", "")
        style = color_map.get(feu, "")
        return pd.Series([style] * len(row), index=row.index)

    return df.style.apply(style_row, axis=1)


_DISPLAY_COLS = [
    "N Commande", "Ligne", "Code Client", "Nom Client",
    "Article", "Designation",
    "Qte Commandee", "Qte Restante",
    "Date Expedition", "Date Besoin Prod",
    "OF Associe", "Source Couverture",
    "Feu Matiere", "Feu Capacite", "Feu Ligne",
    "Lancable", "Alerte",
]

_FEU_COLS = {"Feu Matiere", "Feu Capacite", "Feu Ligne"}


def _esc(value: object) -> str:
    return str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _badge(feu: str) -> str:
    cfg = {
        "VERT":   ("var(--green-bg)",  "var(--green-fg)",  "var(--green-ring)",  "●", "Vert"),
        "ORANGE": ("var(--amber-bg)",  "var(--amber-fg)",  "var(--amber-ring)",  "●", "Orange"),
        "ROUGE":  ("var(--red-bg)",    "var(--red-fg)",    "var(--red-ring)",    "●", "Rouge"),
    }
    if feu not in cfg:
        return f'<span class="badge badge-neutral">{_esc(feu)}</span>'
    bg, fg, ring, dot, label = cfg[feu]
    return (
        f'<span class="badge" style="background:{bg};color:{fg};box-shadow:0 0 0 1px {ring};">'
        f'<span style="color:{fg};margin-right:4px;">{dot}</span>{label}'
        f'</span>'
    )


def render_commandes_expandable(df: pd.DataFrame) -> None:
    if df.empty:
        st.info("Aucune commande.")
        return

    df = format_dates_for_display(df)
    cols = [c for c in _DISPLAY_COLS if c in df.columns]

    groups_with_children: set[int] = set()
    tmp_group = 0
    for _, row in df.iterrows():
        is_se = str(row.get("Type Flux", "")) == "sous-ensemble"
        if not is_se:
            tmp_group += 1
        else:
            groups_with_children.add(tmp_group)

    thead_ths = '<th class="th-toggle"></th>' + "".join(
        f'<th class="th">{_esc(c)}</th>' for c in cols
    )

    tbody_rows: list[str] = []
    group_id = 0

    for _, row in df.iterrows():
        is_se = str(row.get("Type Flux", "")) == "sous-ensemble"
        feu = str(row.get("Feu Ligne", ""))

        if not is_se:
            group_id += 1
            current_group = group_id

        if not is_se:
            row_cls = {"VERT": "row-green", "ORANGE": "row-amber", "ROUGE": "row-red"}.get(feu, "row-base")
        else:
            row_cls = "row-child"

        cells = "".join(
            f'<td class="td">{_badge(str(row[c]))}</td>'
            if c in _FEU_COLS
            else f'<td class="td">{_esc(row[c])}</td>'
            for c in cols
        )

        if not is_se:
            if current_group in groups_with_children:
                btn = (
                    f'<td class="td-toggle">'
                    f'<button class="toggle-btn" data-gid="{current_group}" onclick="toggle(this)">+'
                    f'</button></td>'
                )
            else:
                btn = '<td class="td-toggle"></td>'
            tbody_rows.append(f'<tr class="tr {row_cls}">{btn}{cells}</tr>')
        else:
            tbody_rows.append(
                f'<tr class="tr row-child" data-group="{current_group}">'
                f'<td class="td-toggle td-child-indent"></td>{cells}</tr>'
            )

    n_rows = len(df)
    height = min(max(n_rows * 36 + 52, 200), 640)

    html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
  :root {{
    --font: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    --radius: 8px;
    --border: #e4e4e7;
    --header-bg: #18181b;
    --header-fg: #fafafa;
    --row-hover: #f4f4f5;
    --green-bg: #f0fdf4; --green-fg: #15803d; --green-ring: #bbf7d0; --green-row: #f0fdf4;
    --amber-bg: #fffbeb; --amber-fg: #b45309; --amber-ring: #fde68a; --amber-row: #fffbeb;
    --red-bg:   #fff1f2; --red-fg:   #be123c; --red-ring:   #fecdd3; --red-row:   #fff1f2;
    --child-bg: #f8fafc;
    --child-fg: #475569;
    --child-border: #e2e8f0;
  }}
  *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: var(--font); font-size: 13px; background: #fff; }}

  .wrapper {{
    border: 1px solid var(--border);
    border-radius: var(--radius);
    overflow: hidden;
    overflow-x: auto;
    max-height: {height}px;
    overflow-y: auto;
    box-shadow: 0 1px 3px 0 rgb(0 0 0 / .06), 0 1px 2px -1px rgb(0 0 0 / .06);
  }}

  table {{
    border-collapse: collapse;
    width: 100%;
    min-width: 900px;
  }}

  thead {{ position: sticky; top: 0; z-index: 2; }}

  .th-toggle {{
    width: 36px;
    background: var(--header-bg);
    border-bottom: 1px solid #3f3f46;
  }}
  .th {{
    padding: 10px 12px;
    text-align: left;
    font-size: 11px;
    font-weight: 600;
    letter-spacing: .06em;
    text-transform: uppercase;
    color: var(--header-fg);
    background: var(--header-bg);
    border-bottom: 1px solid #3f3f46;
    white-space: nowrap;
  }}

  .tr {{ border-bottom: 1px solid var(--border); transition: background .1s; }}
  .tr:last-child {{ border-bottom: none; }}

  .row-base:hover  {{ background: var(--row-hover); }}
  .row-green       {{ background: var(--green-row); }}
  .row-green:hover {{ background: #dcfce7; }}
  .row-amber       {{ background: var(--amber-row); }}
  .row-amber:hover {{ background: #fef3c7; }}
  .row-red         {{ background: var(--red-row); }}
  .row-red:hover   {{ background: #ffe4e6; }}

  .row-child {{
    background: var(--child-bg);
    border-left: 3px solid #cbd5e1;
  }}
  .row-child .td {{ color: var(--child-fg); font-style: italic; font-size: 12px; }}

  .td {{
    padding: 8px 12px;
    white-space: nowrap;
    vertical-align: middle;
  }}
  .td-toggle {{
    width: 36px;
    text-align: center;
    vertical-align: middle;
    padding: 0 4px;
  }}
  .td-child-indent {{ background: var(--child-bg); }}

  .toggle-btn {{
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 20px;
    height: 20px;
    border-radius: 4px;
    border: 1px solid #d4d4d8;
    background: #fff;
    color: #71717a;
    font-size: 14px;
    font-weight: 700;
    line-height: 1;
    cursor: pointer;
    transition: background .12s, color .12s, border-color .12s;
    padding: 0;
  }}
  .toggle-btn:hover {{
    background: #f4f4f5;
    border-color: #a1a1aa;
    color: #18181b;
  }}
  .toggle-btn.open {{
    background: #18181b;
    border-color: #18181b;
    color: #fff;
  }}

  .badge {{
    display: inline-flex;
    align-items: center;
    padding: 2px 8px;
    border-radius: 9999px;
    font-size: 11px;
    font-weight: 600;
    letter-spacing: .03em;
    white-space: nowrap;
  }}
  .badge-neutral {{
    background: #f4f4f5;
    color: #52525b;
    box-shadow: 0 0 0 1px #e4e4e7;
  }}
</style>
</head>
<body>
<div class="wrapper">
  <table>
    <thead><tr>{thead_ths}</tr></thead>
    <tbody>{"".join(tbody_rows)}</tbody>
  </table>
</div>
<script>
  document.querySelectorAll('tr[data-group]').forEach(function(r) {{ r.style.display = 'none'; }});
  function toggle(btn) {{
    var gid = btn.getAttribute('data-gid');
    var rows = document.querySelectorAll('tr[data-group="' + gid + '"]');
    var isOpen = btn.classList.contains('open');
    rows.forEach(function(r) {{ r.style.display = isOpen ? 'none' : ''; }});
    btn.classList.toggle('open', !isOpen);
    btn.textContent = isOpen ? '+' : '−';
  }}
</script>
</body>
</html>"""
    components.html(html, height=height + 20, scrolling=False)


def render_week_focus_gantt(df_charge_segments: pd.DataFrame, df_plan: pd.DataFrame) -> None:
    if df_charge_segments.empty or df_plan.empty:
        st.warning("Aucune charge OF a afficher.")
        return

    weeks = sorted(
        df_charge_segments["Semaine"].dropna().astype(str).drop_duplicates().tolist(),
        key=lambda label: parse_week_bounds(label)[0],
    )
    week_index_key = "selected_week_index"
    if week_index_key not in st.session_state or st.session_state[week_index_key] >= len(weeks):
        st.session_state[week_index_key] = 0

    nav_left, nav_center, nav_right = st.columns([1, 4, 1])
    with nav_left:
        if st.button("◀", use_container_width=True, disabled=st.session_state[week_index_key] == 0):
            st.session_state[week_index_key] -= 1
            st.rerun()
    with nav_center:
        st.markdown(f"### {weeks[st.session_state[week_index_key]]}")
    with nav_right:
        if st.button("▶", use_container_width=True, disabled=st.session_state[week_index_key] >= len(weeks) - 1):
            st.session_state[week_index_key] += 1
            st.rerun()

    selected_week = weeks[st.session_state[week_index_key]]
    week_start, week_end = parse_week_bounds(selected_week)

    week_rows = df_charge_segments[df_charge_segments["Semaine"].astype(str) == selected_week].copy()
    if week_rows.empty:
        st.warning("Aucune charge OF sur la semaine selectionnee.")
        return

    postes = sorted(df_charge_segments["Poste"].dropna().astype(str).drop_duplicates().tolist())
    postes_key = "selected_postes_week_focus"
    postes_par_defaut = [poste for poste in ["PP_128", "PP_830"] if poste in postes]
    if postes_key not in st.session_state:
        st.session_state[postes_key] = postes_par_defaut if postes_par_defaut else postes[:2]
    else:
        st.session_state[postes_key] = [poste for poste in st.session_state[postes_key] if poste in postes]
        if not st.session_state[postes_key] and postes_par_defaut:
            st.session_state[postes_key] = postes_par_defaut

    selected_postes = st.multiselect(
        "Postes de charge",
        options=postes,
        key=postes_key,
    )
    if selected_postes:
        week_rows = week_rows[week_rows["Poste"].astype(str).isin(selected_postes)].copy()
    if week_rows.empty:
        st.warning("Aucune charge sur les postes selectionnes.")
        return

    capacity_row = df_plan[df_plan["Semaine"].astype(str) == selected_week]
    capacity_h = float(capacity_row.iloc[0]["Capa (h/sem)"]) if not capacity_row.empty else 0.0
    week_rows["Charge (%)"] = (week_rows["Charge (h)"] / capacity_h * 100).round(1) if capacity_h > 0 else 0.0
    week_rows["Date besoin tri"] = pd.to_datetime(week_rows["Date besoin cmd"], errors="coerce")
    week_rows["Date debut tri"] = pd.to_datetime(week_rows["Date debut OF"], errors="coerce")
    week_rows["Date fin tri"] = pd.to_datetime(week_rows["Date fin OF"], errors="coerce")
    week_rows["Statut affichage"] = week_rows["Statut OF"].astype(str).replace(
        {"Lancé": "Ferme", "Planifie": "Planifié", "Suggere": "Suggéré"}
    )

    daily_view = (
        week_rows.groupby(
            [
                "Poste",
                "MFGNUM",
                "Article",
                "Designation",
                "Statut OF",
                "Statut affichage",
                "Date besoin tri",
                "Date debut tri",
                "Date fin tri",
            ],
            dropna=False,
            as_index=False,
        )
        .agg(
            {
                "Date": ["min", "max"],
                "Charge (h)": "sum",
                "Charge (%)": "sum",
            }
        )
    )
    daily_view.columns = [
        "Poste",
        "MFGNUM",
        "Article",
        "Designation",
        "Statut OF",
        "Statut affichage",
        "Date besoin tri",
        "Date debut tri",
        "Date fin tri",
        "Date debut semaine",
        "Date fin semaine",
        "Charge (h)",
        "Charge (%)",
    ]
    charge_jour_h = capacity_h / 5 if capacity_h > 0 else 1.0
    daily_view["Duree affichee (j)"] = daily_view["Charge (h)"].apply(
        lambda value: round(float(value) / charge_jour_h, 3) if charge_jour_h > 0 else 0.0
    )
    daily_view["Segment debut jour"] = pd.to_datetime(daily_view["Date debut semaine"], errors="coerce")
    daily_view["Segment fin jour"] = daily_view["Segment debut jour"] + pd.to_timedelta(daily_view["Duree affichee (j)"], unit="D")
    week_end_exclusive = week_end + pd.Timedelta(days=1)
    daily_view["Segment fin jour"] = daily_view["Segment fin jour"].clip(upper=week_end_exclusive)
    daily_view["Date debut affichee"] = daily_view[["Date debut tri", "Date debut semaine"]].min(axis=1)
    daily_view["Date fin affichee"] = daily_view[["Date fin tri", "Date fin semaine"]].max(axis=1)

    poste_totals = daily_view.groupby("Poste", as_index=False).agg({"Charge (h)": "sum"})
    poste_totals["Charge totale ligne"] = poste_totals["Charge (h)"].map(lambda value: f"{float(value):.1f} h")
    poste_order = [
        row["Poste"]
        for _, row in sorted(
            poste_totals.iterrows(),
            key=lambda item: float(item[1]["Charge (h)"]),
            reverse=True,
        )
    ]
    chart_height = min(max(len(poste_order) * 42, 360), 1400)
    label_x = week_end + pd.Timedelta(hours=12)
    domain_end = week_end + pd.Timedelta(hours=20)

    bars = (
        alt.Chart(daily_view)
        .mark_bar(size=24, stroke="#0b1220", strokeWidth=1.5, cornerRadius=6)
        .encode(
            x=alt.X("Segment debut jour:T", title="Semaine", scale=alt.Scale(domain=[week_start, domain_end]), axis=alt.Axis(format="%d/%m/%Y")),
            x2="Segment fin jour:T",
            y=alt.Y("Poste:N", sort=poste_order, title="Poste de charge"),
            color=alt.Color(
                "Statut affichage:N",
                title="Statut OF",
                scale=alt.Scale(
                    domain=["Ferme", "Planifié", "Suggéré"],
                    range=["#16a34a", "#f59e0b", "#9ca3af"],
                ),
            ),
            tooltip=[
                alt.Tooltip("Poste:N", title="Poste"),
                alt.Tooltip("MFGNUM:N", title="OF"),
                alt.Tooltip("Article:N", title="Article"),
                alt.Tooltip("Designation:N", title="Designation"),
                alt.Tooltip("Statut OF:N", title="Statut OF"),
                alt.Tooltip("Date debut affichee:T", title="Date debut OF", format="%d/%m/%Y"),
                alt.Tooltip("Date besoin tri:T", title="Date besoin cmd", format="%d/%m/%Y"),
                alt.Tooltip("Date fin affichee:T", title="Date fin OF", format="%d/%m/%Y"),
                alt.Tooltip("Charge (h):Q", title="Charge (h)", format=".1f"),
                alt.Tooltip("Charge (%):Q", title="Charge / sem (%)", format=".1f"),
                alt.Tooltip("Duree affichee (j):Q", title="Duree affichee (j)", format=".0f"),
                alt.Tooltip("Date debut semaine:T", title="Debut dans la semaine", format="%d/%m/%Y"),
                alt.Tooltip("Date fin semaine:T", title="Fin dans la semaine", format="%d/%m/%Y"),
            ],
        )
        .properties(height=chart_height)
    )

    labels_df = poste_totals.copy()
    labels_df["LabelX"] = label_x
    labels = (
        alt.Chart(labels_df)
        .mark_text(align="left", dx=8, baseline="middle", color="#e5e7eb")
        .encode(
            x="LabelX:T",
            y=alt.Y("Poste:N", sort=poste_order),
            text="Charge totale ligne:N",
        )
    )

    chart = bars + labels

    st.altair_chart(chart, use_container_width=True)
    st.caption(f"Charge hebdomadaire de reference: {capacity_h:.1f} h par poste")

    detail_cols = [
        "Poste",
        "MFGNUM",
        "Article",
        "Designation",
        "Statut OF",
        "Date debut affichee",
        "Date besoin tri",
        "Date fin affichee",
        "Charge (h)",
        "Duree affichee (j)",
        "Date debut semaine",
        "Date fin semaine",
    ]
    st.dataframe(format_dates_for_display(daily_view[detail_cols]), use_container_width=True, height=380)


def main() -> None:
    st.set_page_config(
        page_title="Ordonnancement OF",
        page_icon="🏭",
        layout="wide",
        initial_sidebar_state="expanded",
    )

    st.title("Ordonnancement OF Aereco")
    st.caption("Interface Streamlit branchee sur `moteur_ordonnancement.py`")

    if "results" not in st.session_state:
        st.session_state["results"] = None
    if "last_error" not in st.session_state:
        st.session_state["last_error"] = None
    if "last_run_at" not in st.session_state:
        st.session_state["last_run_at"] = None

    project_root = Path(__file__).parent.resolve()
    default_data_dir = str((project_root / "data").resolve())
    default_output_dir = str((project_root / "output").resolve())

    with st.sidebar:
        st.header("Parametres")
        with st.form("run_form"):
            data_dir = st.text_input("Dossier data", value=default_data_dir)
            output_dir = st.text_input("Dossier output", value=default_output_dir)
            present_files, missing_files = list_data_files(data_dir)
            if missing_files:
                st.error("Fichiers manquants")
                st.write("- " + "\n- ".join(missing_files))
            else:
                st.success(f"{len(present_files)} fichiers detectes")

            date_reference = st.date_input("Date de reference", value=datetime.now())
            horizon_jours = st.slider("Horizon (jours)", min_value=7, max_value=180, value=int(PARAMS["horizon_jours"]))
            jours_ouvres = st.slider(
                "Jours ouvres avant expedition",
                min_value=0,
                max_value=10,
                value=int(PARAMS["jours_ouvres_avant_expedition"]),
            )
            capacite_heures_semaine = st.slider(
                "Capacite heures / semaine",
                min_value=10,
                max_value=200,
                value=int(PARAMS["capacite_heures_semaine"]),
            )
            allocation_sequentielle = st.toggle("Allocation sequentielle", value=bool(PARAMS["allocation_sequentielle"]))
            deduire_allocations = st.toggle("Deduire allocations existantes", value=bool(PARAMS["deduire_allocations"]))
            perimetre_chargement = st.selectbox(
                "Perimetre chargement",
                options=["tous", "commandes"],
                index=1,
            )
            mode_injection = st.selectbox(
                "Mode injection",
                options=["conservateur"],
                index=0,
            )
            separateur_csv = st.selectbox("Separateur CSV", options=[";", ",", "\t"], index=0)
            encoding = st.selectbox("Encoding", options=["latin-1", "utf-8", "cp1252"], index=0)
            run_clicked = st.form_submit_button("Lancer l'ordonnancement", type="primary", use_container_width=True)

    params = build_params(
        data_dir=data_dir,
        output_dir=output_dir,
        horizon_jours=horizon_jours,
        jours_ouvres=jours_ouvres,
        capacite_heures_semaine=capacite_heures_semaine,
        allocation_sequentielle=allocation_sequentielle,
        deduire_allocations=deduire_allocations,
        perimetre_chargement=perimetre_chargement,
        mode_injection=mode_injection,
        separateur_csv=separateur_csv,
        encoding=encoding,
        date_reference=datetime.combine(date_reference, datetime.min.time()),
    )

    if run_clicked:
        st.session_state["last_error"] = None
        if missing_files:
            st.session_state["results"] = None
            st.session_state["last_error"] = "Impossible de lancer: le dossier data ne contient pas tous les CSV attendus."
        else:
            with st.spinner("Execution du moteur en cours..."):
                try:
                    df_cmd, df_det, df_plan, df_crit, df_charge_segments, logs = run_engine(params)
                    excel_bytes = build_excel_bytes(df_cmd, df_det, df_plan, df_crit, params)
                    st.session_state["results"] = {
                        "df_cmd": df_cmd,
                        "df_det": df_det,
                        "df_plan": df_plan,
                        "df_crit": df_crit,
                        "df_charge_segments": df_charge_segments,
                        "logs": logs,
                        "excel_bytes": excel_bytes,
                    }
                    st.session_state["params"] = params
                    st.session_state["last_run_at"] = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
                    st.rerun()
                except Exception as exc:
                    st.session_state["results"] = None
                    st.session_state["last_error"] = str(exc)
                    st.rerun()

    results = st.session_state.get("results")

    if st.session_state.get("last_error"):
        st.error(st.session_state["last_error"])

    if results is None:
        st.info("Configure les parametres dans la barre laterale puis lance l'ordonnancement.")
        return

    df_cmd = results["df_cmd"]
    df_det = results["df_det"]
    df_plan = results["df_plan"]
    df_crit = results["df_crit"]
    df_charge_segments = results["df_charge_segments"]
    logs = results["logs"]
    summary = summarize_commandes(df_cmd)

    if st.session_state.get("last_run_at"):
        st.success(f"Derniere execution terminee: {st.session_state['last_run_at']}")

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Lignes vertes", summary["VERT"])
    col2.metric("Lignes orange", summary["ORANGE"])
    col3.metric("Lignes rouges", summary["ROUGE"])
    col4.metric("Sous-ensembles", summary["sous_ensembles"])

    download_name = f"ordonnancement_of_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
    st.download_button(
        label="Telecharger le fichier Excel",
        data=results["excel_bytes"],
        file_name=download_name,
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=True,
    )

    tabs = st.tabs([
        "Commandes",
        "Detail manquants",
        "Gantt charge hebdo",
        "Plan de charge",
        "Composants critiques",
        "Logs",
    ])

    with tabs[0]:
        st.subheader("Vue commandes")
        render_commandes_expandable(df_cmd)

    with tabs[1]:
        st.subheader("Detail des manquants")
        if df_det.empty:
            st.success("Aucun composant manquant sur ce run.")
        else:
            st.dataframe(format_dates_for_display(df_det), use_container_width=True, height=560)

    with tabs[2]:
        st.subheader("Vision semaine - OF par poste de charge")
        render_week_focus_gantt(df_charge_segments, df_plan)

    with tabs[3]:
        st.subheader("Plan de charge")
        if df_plan.empty:
            st.warning("Aucun plan de charge genere.")
        else:
            poste_labels = load_poste_labels(params)
            capacity_by_week = {
                str(row["Semaine"]): float(row["Capa (h/sem)"])
                for _, row in df_plan[["Semaine", "Capa (h/sem)"]].iterrows()
            }
            display_mode = st.radio(
                "Affichage",
                options=["Heures", "Pourcentage (%)"],
                horizontal=True,
                key="plan_charge_display_mode",
            )
            suffix = " (h)" if display_mode == "Heures" else " (%)"
            plan_matrix = build_plan_charge_matrix(df_plan, suffix)
            plan_matrix = label_plan_charge_index(plan_matrix, poste_labels)
            styled_plan_matrix = style_plan_charge_matrix(plan_matrix, capacity_by_week, display_mode)
            st.dataframe(styled_plan_matrix, use_container_width=True, height=560)

    with tabs[4]:
        st.subheader("Composants critiques")
        if df_crit.empty:
            st.success("Aucun composant critique detecte.")
        else:
            st.dataframe(format_dates_for_display(df_crit), use_container_width=True, height=560)

    with tabs[5]:
        st.subheader("Logs moteur")
        st.code(logs or "Aucun log.", language="text")


if __name__ == "__main__":
    main()
