from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Optional, Any

import pandas as pd


def _calc_stock_dispo(stock: pd.DataFrame, itmref: str) -> float:
    row = stock[stock["itmref"] == itmref]
    if row.empty:
        return 0.0
    r = row.iloc[0]
    dispo = r.get("dispo_instantane", r["stock_physique"] - r.get("stock_alloue", 0) - r.get("stock_bloque", 0))
    return float(dispo) if pd.notna(dispo) else 0.0


def _get_reception_info(
    itmref: str,
    receptions_oa: pd.DataFrame,
    receptions_of: pd.DataFrame,
) -> dict:
    prd = None
    prs = ""
    prq = 0.0
    oa = receptions_oa[
        (receptions_oa["itmref"] == itmref) &
        (receptions_oa["qte_restante"] > 0) &
        (receptions_oa["date_reception"].notna())
    ]
    if not oa.empty:
        bo = oa.sort_values("date_reception").iloc[0]
        prd = bo["date_reception"]
        prs = f"OA {bo['pohnum']}"
        prq = float(bo["qte_restante"])
    rof = receptions_of[
        (receptions_of["itmref"] == itmref) &
        (receptions_of["qte_restante"] > 0) &
        (receptions_of["enddat"].notna())
    ]
    if not rof.empty:
        br = rof.sort_values("enddat").iloc[0]
        if prd is None or br["enddat"] < prd:
            prd = br["enddat"]
            prs = f"OF {br['mfgnum']}"
            prq = float(br["qte_restante"])
    return {"date": prd, "source": prs, "qte": prq}


def construire_chaine_commande(
    sohnum: str,
    soplin: str,
    dfs: Dict[str, pd.DataFrame],
    profondeur_max: int = 5,
) -> dict:
    commandes = dfs.get("commandes", pd.DataFrame())
    if commandes.empty:
        return {"error": "Aucune donnee commande"}
    cmd = commandes[
        (commandes["sohnum"] == sohnum) &
        (commandes["soplin"].astype(str) == str(soplin))
    ]
    if cmd.empty:
        return {"error": f"Commande {sohnum}/{soplin} non trouvee"}
    row = cmd.iloc[0]
    return _construire_noeud_commande(row, dfs, profondeur_max)


def _construire_noeud_commande(
    cmd_row: pd.Series,
    dfs: Dict[str, pd.DataFrame],
    profondeur_max: int,
) -> dict:
    stock = dfs.get("stock", pd.DataFrame())
    of_entetes = dfs.get("of_entetes", pd.DataFrame())
    receptions_oa = dfs.get("receptions_oa", pd.DataFrame())
    receptions_of = dfs.get("receptions_of", pd.DataFrame())
    of_composants = dfs.get("of_composants", pd.DataFrame())
    articles = dfs.get("articles", pd.DataFrame())

    itmref = cmd_row["itmref"]
    qte_restante = cmd_row["qte_restante"]
    client_code = cmd_row.get("client_code", "")
    client_nom = cmd_row.get("client_nom", "")
    shidat = cmd_row.get("shidat")
    mfgnum_lie = str(cmd_row.get("mfgnum_lie", "")).strip()
    has_of_lie = mfgnum_lie and mfgnum_lie not in ["", "nan", "None"]

    art_info = articles[articles["itmref"] == itmref]
    designation = art_info.iloc[0].get("designation", "") if not art_info.empty else ""

    stock_dispo = _calc_stock_dispo(stock, itmref)
    stock_suffisant = stock_dispo >= qte_restante

    noeud: dict = {
        "type": "commande",
        "id": f"{cmd_row['sohnum']}/{cmd_row['soplin']}",
        "sohnum": cmd_row["sohnum"],
        "soplin": cmd_row["soplin"],
        "client_code": client_code,
        "client_nom": client_nom,
        "itmref": itmref,
        "designation": designation,
        "qte_restante": float(qte_restante),
        "shidat": shidat,
        "is_contremarque": has_of_lie,
        "stock_dispo": stock_dispo,
        "stock_suffisant": stock_suffisant,
        "feu": "VERT" if stock_suffisant else "ROUGE",
        "enfants": [],
    }

    if stock_suffisant:
        noeud["source"] = "stock"
        noeud["message"] = f"Stock disponible: {stock_dispo:.0f}"
        return noeud

    if has_of_lie:
        of_info = of_entetes[of_entetes["mfgnum"] == mfgnum_lie]
        if of_info.empty:
            noeud["message"] = f"OF lié {mfgnum_lie} non trouvé"
            noeud["source"] = "of_contremarque"
            noeud["feu"] = "ROUGE"
            return noeud
        noeud_of = _construire_noeud_of(mfgnum_lie, dfs, qte_restante, profondeur_max, 0)
        noeud["enfants"] = [noeud_of]
        noeud["source"] = "of_contremarque"
        noeud["feu"] = noeud_of.get("feu", "ROUGE")
        return noeud

    of_candidats = of_entetes[
        (of_entetes["itmref"] == itmref) &
        (of_entetes["qte_restante"] > 0)
    ].copy()
    if not of_candidats.empty:
        of_candidats = of_candidats.sort_values("enddat")
        meilleurs_of = of_candidats.head(3)
        enfants_of = []
        for _, of_row in meilleurs_of.iterrows():
            noeud_of = _construire_noeud_of(
                of_row["mfgnum"], dfs, qte_restante, profondeur_max, 0
            )
            enfants_of.append(noeud_of)
        if enfants_of:
            noeud["enfants"] = enfants_of
            noeud["source"] = "of_disponibles"
            meilleurs_feux = [e.get("feu", "ROUGE") for e in enfants_of]
            if "VERT" in meilleurs_feux:
                noeud["feu"] = "VERT"
            elif "ORANGE" in meilleurs_feux:
                noeud["feu"] = "ORANGE"
            else:
                noeud["feu"] = "ROUGE"

    if not noeud["enfants"]:
        rec_info = _get_reception_info(itmref, receptions_oa, receptions_of)
        if rec_info["date"] is not None:
            noeud["reception"] = rec_info
            noeud["source"] = "reception_attendue"
            noeud["feu"] = "ORANGE"
            noeud["message"] = f"Reception {rec_info['source']} le {rec_info['date'].strftime('%d/%m/%Y')}"
        else:
            noeud["source"] = "aucun_appro"
            noeud["feu"] = "ROUGE"
            noeud["message"] = "Aucun approvisionnement identifie"

    return noeud


def _construire_noeud_of(
    mfgnum: str,
    dfs: Dict[str, pd.DataFrame],
    qte_parent: float,
    profondeur_max: int,
    profondeur: int,
) -> dict:
    stock = dfs.get("stock", pd.DataFrame())
    of_entetes = dfs.get("of_entetes", pd.DataFrame())
    of_composants = dfs.get("of_composants", pd.DataFrame())
    receptions_oa = dfs.get("receptions_oa", pd.DataFrame())
    receptions_of = dfs.get("receptions_of", pd.DataFrame())
    articles = dfs.get("articles", pd.DataFrame())

    of_info = of_entetes[of_entetes["mfgnum"] == mfgnum]
    if of_info.empty:
        return {
            "type": "of",
            "mfgnum": mfgnum,
            "error": "OF non trouve",
            "feu": "ROUGE",
            "enfants": [],
        }

    of_row = of_info.iloc[0]
    itmref = of_row["itmref"]
    designation = of_row.get("designation", "")
    enddat = of_row.get("enddat")
    qte_restante = of_row.get("qte_restante", 0)
    mfgsta = of_row.get("mfgsta", "")
    mfgsta_lib = of_row.get("mfgsta_lib", "")

    art_info = articles[articles["itmref"] == itmref]
    categorie = art_info.iloc[0].get("categorie", "") if not art_info.empty else ""

    noeud: dict = {
        "type": "of",
        "mfgnum": mfgnum,
        "itmref": itmref,
        "designation": designation,
        "categorie": categorie,
        "enddat": enddat,
        "qte_restante": float(qte_restante),
        "mfgsta": mfgsta,
        "mfgsta_lib": mfgsta_lib,
        "feu": "VERT",
        "composants": [],
        "enfants": [],
    }

    comp_of = of_composants[of_composants["mfgnum"] == mfgnum]
    if comp_of.empty:
        noeud["message"] = "OF sans composants"
        return noeud

    comp_agg = comp_of.groupby("composant", as_index=False).agg({
        "qty_requise": "sum",
        "designation": "first",
        "dat_besoin": "min",
    })

    composants_nodes: List[dict] = []
    nb_vert = 0
    nb_orange = 0
    nb_rouge = 0

    for _, comp in comp_agg.iterrows():
        comp_ref = comp["composant"]
        qty_req = comp["qty_requise"]
        comp_designation = comp.get("designation", "")
        dat_besoin = comp.get("dat_besoin")

        art_comp = articles[articles["itmref"] == comp_ref]
        comp_categorie = art_comp.iloc[0].get("categorie", "") if not art_comp.empty else ""

        stock_comp = _calc_stock_dispo(stock, comp_ref)
        stock_ok = stock_comp >= qty_req

        comp_node: dict = {
            "type": "composant",
            "itmref": comp_ref,
            "designation": comp_designation,
            "categorie": comp_categorie,
            "qty_requise": float(qty_req),
            "stock_dispo": stock_comp,
            "dat_besoin": dat_besoin,
            "feu": "VERT" if stock_ok else "ROUGE",
            "enfants": [],
        }

        if stock_ok:
            nb_vert += 1
        else:
            rec_info = _get_reception_info(comp_ref, receptions_oa, receptions_of)
            if rec_info["date"] is not None:
                comp_node["reception"] = rec_info
                comp_node["feu"] = "ORANGE"
                nb_orange += 1
            else:
                if profondeur < profondeur_max:
                    of_enfants = of_entetes[
                        (of_entetes["itmref"] == comp_ref) &
                        (of_entetes["qte_restante"] > 0)
                    ].copy()
                    if not of_enfants.empty:
                        of_enfant = of_enfants.sort_values("enddat").iloc[0]
                        noeud_enfant = _construire_noeud_of(
                            of_enfant["mfgnum"], dfs, qty_req, profondeur_max, profondeur + 1
                        )
                        comp_node["enfants"] = [noeud_enfant]
                        if noeud_enfant.get("feu") == "VERT":
                            comp_node["feu"] = "ORANGE"
                            nb_orange += 1
                        else:
                            nb_rouge += 1
                    else:
                        nb_rouge += 1
                else:
                    nb_rouge += 1

        composants_nodes.append(comp_node)

    noeud["composants"] = composants_nodes

    total = len(composants_nodes)
    if total == 0:
        noeud["feu"] = "VERT"
    elif nb_rouge > 0:
        noeud["feu"] = "ROUGE"
    elif nb_orange > 0:
        noeud["feu"] = "ORANGE"
    else:
        noeud["feu"] = "VERT"

    return noeud


def generer_graphviz(noeud: dict) -> str:
    lines: List[str] = []
    lines.append("digraph SupplyChain {")
    lines.append("  rankdir=TB;")
    lines.append("  node [shape=box, style=\"rounded,filled\", fontname=\"Arial\", fontsize=10];")
    lines.append("  edge [fontname=\"Arial\", fontsize=9];")
    lines.append("")

    def get_color(feu: str) -> str:
        colors = {
            "VERT": "#dcfce7",
            "ORANGE": "#fde68a",
            "ROUGE": "#fecaca",
        }
        return colors.get(feu, "#f4f4f5")

    def get_border(feu: str) -> str:
        borders = {
            "VERT": "#166534",
            "ORANGE": "#b45309",
            "ROUGE": "#be123c",
        }
        return borders.get(feu, "#71717a")

    def escape_label(text: str) -> str:
        return str(text).replace('"', '\\"').replace("\n", "\\n")

    node_counter = [0]

    def add_node(n: dict, parent_id: Optional[str] = None, edge_label: str = "") -> str:
        node_counter[0] += 1
        node_id = f"n{node_counter[0]}"
        ntype = n.get("type", "unknown")
        feu = n.get("feu", "ROUGE")
        fillcolor = get_color(feu)
        bordercolor = get_border(feu)

        if ntype == "commande":
            label = f"COMMANDE\\n{n.get('id', '')}\\n{n.get('client_nom', '')}\\n{n.get('itmref', '')}\\nQté: {n.get('qte_restante', 0):.0f}"
            shape = "box"
        elif ntype == "of":
            label = f"OF: {n.get('mfgnum', '')}\\n{n.get('itmref', '')}\\n{n.get('designation', '')[:20]}\\nQté: {n.get('qte_restante', 0):.0f}\\nFin: {n.get('enddat', '').strftime('%d/%m/%Y') if pd.notna(n.get('enddat')) else 'N/A'}"
            shape = "box"
        elif ntype == "composant":
            label = f"COMP: {n.get('itmref', '')}\\n{n.get('designation', '')[:20] if n.get('designation') else ''}\\nReq: {n.get('qty_requise', 0):.0f}\\nStock: {n.get('stock_dispo', 0):.0f}"
            shape = "box"
        else:
            label = str(n.get("id", ntype))
            shape = "box"

        lines.append(
            f'  {node_id} [label="{escape_label(label)}", '
            f'fillcolor="{fillcolor}", color="{bordercolor}", '
            f'penwidth=2, shape={shape}];'
        )

        if parent_id:
            if edge_label:
                lines.append(f'  {parent_id} -> {node_id} [label="{escape_label(edge_label)}"];')
            else:
                lines.append(f"  {parent_id} -> {node_id};")

        enfants = n.get("enfants", [])
        for i, enfant in enumerate(enfants):
            edge = f"OF {i+1}" if len(enfants) > 1 else ""
            add_node(enfant, node_id, edge)

        composants = n.get("composants", [])
        for comp in composants:
            comp_id = add_node(comp, node_id, "")
            sous_enfants = comp.get("enfants", [])
            for sous in sous_enfants:
                add_node(sous, comp_id, "")

        return node_id

    add_node(noeud)

    lines.append("}")
    return "\n".join(lines)


def generer_html_tree(noeud: dict) -> str:
    def get_color(feu: str) -> tuple:
        colors = {
            "VERT": ("#dcfce7", "#166534", "●"),
            "ORANGE": ("#fde68a", "#b45309", "●"),
            "ROUGE": ("#fecaca", "#be123c", "●"),
        }
        return colors.get(feu, ("#f4f4f5", "#71717a", "○"))

    def escape_html(text: str) -> str:
        return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")

    def render_node(n: dict, level: int = 0) -> str:
        ntype = n.get("type", "unknown")
        feu = n.get("feu", "ROUGE")
        bg, fg, dot = get_color(feu)
        indent = level * 24

        if ntype == "commande":
            title = f"COMMANDE {n.get('id', '')}"
            details = [
                f"Client: {n.get('client_nom', '')}",
                f"Article: {n.get('itmref', '')}",
                f"Qté: {n.get('qte_restante', 0):.0f}",
                f"Stock dispo: {n.get('stock_dispo', 0):.0f}",
            ]
            if n.get("message"):
                details.append(n["message"])
        elif ntype == "of":
            enddat = n.get("enddat")
            date_str = enddat.strftime("%d/%m/%Y") if pd.notna(enddat) else "N/A"
            title = f"OF {n.get('mfgnum', '')}"
            details = [
                f"Article: {n.get('itmref', '')}",
                f"Désignation: {n.get('designation', '')[:30]}",
                f"Qté: {n.get('qte_restante', 0):.0f}",
                f"Fin: {date_str}",
                f"Statut: {n.get('mfgsta_lib', '')}",
            ]
        elif ntype == "composant":
            title = f"COMP {n.get('itmref', '')}"
            details = [
                f"{n.get('designation', '')[:30]}",
                f"Requis: {n.get('qty_requise', 0):.0f}",
                f"Stock: {n.get('stock_dispo', 0):.0f}",
            ]
            rec = n.get("reception")
            if rec:
                date_str = rec["date"].strftime("%d/%m/%Y") if pd.notna(rec["date"]) else ""
                details.append(f"Réception: {rec['source']} le {date_str}")
        else:
            title = str(n.get("id", ntype))
            details = []

        details_html = "<br>".join(escape_html(d) for d in details)

        children_html = ""
        enfants = n.get("enfants", [])
        composants = n.get("composants", [])

        all_children = list(enfants) + list(composants)
        if all_children:
            children_html = "".join(render_node(child, level + 1) for child in all_children)

        has_children = "has-children" if all_children else ""
        toggle = f'<span class="toggle" onclick="toggleNode(this)">▼</span>' if all_children else '<span class="toggle-placeholder"></span>'

        return f'''
        <div class="tree-node {has_children}" style="margin-left: {indent}px;">
            <div class="node-header" style="background: {bg}; border-left: 4px solid {fg};">
                {toggle}
                <span class="node-status" style="color: {fg};">{dot}</span>
                <span class="node-title" style="color: {fg};">{escape_html(title)}</span>
            </div>
            <div class="node-details">{details_html}</div>
            <div class="node-children">{children_html}</div>
        </div>'''

    html = f'''<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
  :root {{
    --font: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  }}
  body {{
    font-family: var(--font);
    font-size: 13px;
    background: #fff;
    margin: 0;
    padding: 16px;
  }}
  .tree-node {{
    margin-bottom: 4px;
  }}
  .node-header {{
    display: flex;
    align-items: center;
    padding: 8px 12px;
    border-radius: 6px;
    cursor: pointer;
    transition: all 0.15s;
  }}
  .node-header:hover {{
    filter: brightness(0.95);
  }}
  .toggle {{
    width: 16px;
    font-size: 10px;
    color: #71717a;
    cursor: pointer;
    transition: transform 0.2s;
  }}
  .toggle.collapsed {{
    transform: rotate(-90deg);
  }}
  .toggle-placeholder {{
    width: 16px;
  }}
  .node-status {{
    margin: 0 8px;
    font-size: 14px;
  }}
  .node-title {{
    font-weight: 600;
    font-size: 12px;
  }}
  .node-details {{
    padding: 6px 12px 6px 52px;
    font-size: 11px;
    color: #52525b;
    line-height: 1.5;
    background: #fafafa;
    border-radius: 0 0 6px 6px;
  }}
  .node-children {{
    overflow: hidden;
    transition: max-height 0.3s ease-out;
  }}
  .has-children.collapsed .node-children {{
    max-height: 0 !important;
  }}
  .has-children.collapsed .node-details {{
    display: none;
  }}
</style>
</head>
<body>
{render_node(noeud)}
<script>
  function toggleNode(el) {{
    el.classList.toggle('collapsed');
    el.closest('.tree-node').classList.toggle('collapsed');
  }}
  document.querySelectorAll('.tree-node.has-children .node-header').forEach(function(header) {{
    header.addEventListener('click', function(e) {{
      if (!e.target.classList.contains('toggle')) {{
        var toggle = this.querySelector('.toggle');
        if (toggle) toggleNode(toggle);
      }}
    }});
  }});
</script>
</body>
</html>'''
    return html
