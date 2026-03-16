from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd


def _get_semaine_s1(date_ref: datetime) -> Tuple[int, int]:
    iso = date_ref.isocalendar()
    current_week = iso.week
    current_year = iso.year
    s1_week = current_week + 1
    s1_year = current_year
    if s1_week > 52:
        s1_week = 1
        s1_year = current_year + 1
    return s1_week, s1_year


def filtrer_commandes_s1(commandes: pd.DataFrame, date_ref: datetime) -> pd.DataFrame:
    df = commandes.copy()
    df = df[df["shidat"].notna() & (df["qte_restante"] > 0)].copy()
    s1_week, s1_year = _get_semaine_s1(date_ref)
    df["semaine"] = df["shidat"].dt.isocalendar().week
    df["annee"] = df["shidat"].dt.isocalendar().year
    df_s1 = df[(df["semaine"] == s1_week) & (df["annee"] == s1_year)].copy()
    return df_s1


def _calc_stock_dispo(stock: pd.DataFrame, itmref: str) -> float:
    row = stock[stock["itmref"] == itmref]
    if row.empty:
        return 0.0
    r = row.iloc[0]
    dispo = r.get("dispo_instantane", r["stock_physique"] - r.get("stock_alloue", 0) - r.get("stock_bloque", 0))
    return float(dispo) if pd.notna(dispo) else 0.0


def _calc_stock_projete(
    stock: pd.DataFrame,
    receptions_oa: pd.DataFrame,
    receptions_of: pd.DataFrame,
    itmref: str,
    date_limite: datetime,
) -> float:
    dispo = _calc_stock_dispo(stock, itmref)
    oa = receptions_oa[
        (receptions_oa["itmref"] == itmref) &
        (receptions_oa["date_reception"].notna()) &
        (receptions_oa["date_reception"] <= pd.Timestamp(date_limite))
    ]
    dispo += float(oa["qte_restante"].sum())
    rof = receptions_of[
        (receptions_of["itmref"] == itmref) &
        (receptions_of["enddat"].notna()) &
        (receptions_of["enddat"] <= pd.Timestamp(date_limite))
    ]
    dispo += float(rof["qte_restante"].sum())
    return dispo


def evaluer_ligne_sur_stock(
    row: pd.Series,
    stock: pd.DataFrame,
    receptions_oa: pd.DataFrame,
    receptions_of: pd.DataFrame,
    mode: str,
) -> dict:
    itmref = row["itmref"]
    qte = row["qte_restante"]
    qte_allouee = row.get("qte_allouee", 0)
    shidat = row["shidat"]
    if qte <= 0:
        return {
            "satisfaisable": True,
            "stock_dispo": qte_allouee,
            "manquant": 0.0,
            "motif": "Deja couvert par allocation",
        }
    if mode == "immediat":
        stock_dispo = _calc_stock_dispo(stock, itmref)
    else:
        stock_dispo = _calc_stock_projete(stock, receptions_oa, receptions_of, itmref, shidat)
    satisfaisable = stock_dispo >= qte
    manquant = max(0.0, qte - stock_dispo) if not satisfaisable else 0.0
    motif = "" if satisfaisable else f"Stock insuffisant: {stock_dispo:.0f} < {qte:.0f}"
    return {
        "satisfaisable": satisfaisable,
        "stock_dispo": stock_dispo,
        "manquant": manquant,
        "motif": motif,
    }


def evaluer_ligne_mto(
    row: pd.Series,
    of_entetes: pd.DataFrame,
    of_composants: pd.DataFrame,
    stock: pd.DataFrame,
    receptions_oa: pd.DataFrame,
    receptions_of: pd.DataFrame,
    mode: str,
) -> dict:
    mfgnum_lie = str(row.get("mfgnum_lie", "")).strip()
    qte = row["qte_restante"]
    qte_allouee = row.get("qte_allouee", 0)
    shidat = row["shidat"]
    if qte <= 0:
        return {
            "satisfaisable": True,
            "of_existe": True,
            "composants_manquants": [],
            "motif": "Deja couvert par allocation",
        }
    if not mfgnum_lie or mfgnum_lie in ["", "nan", "None"]:
        return {
            "satisfaisable": False,
            "of_existe": False,
            "composants_manquants": [],
            "motif": "Aucun OF lie a cette commande MTO",
        }
    of_info = of_entetes[of_entetes["mfgnum"] == mfgnum_lie]
    if of_info.empty:
        return {
            "satisfaisable": False,
            "of_existe": False,
            "composants_manquants": [],
            "motif": f"OF {mfgnum_lie} non trouve (probablement ferme - stock alloue: {qte_allouee:.0f})",
        }
    comp_of = of_composants[of_composants["mfgnum"] == mfgnum_lie]
    if comp_of.empty:
        return {
            "satisfaisable": True,
            "of_existe": True,
            "composants_manquants": [],
            "motif": "",
        }
    composants_manquants: List[dict] = []
    tous_disponibles = True
    for _, c in comp_of.iterrows():
        comp_ref = c["composant"]
        qty_req = c["qty_requise"]
        if mode == "immediat":
            dispo = _calc_stock_dispo(stock, comp_ref)
        else:
            dat_besoin = c.get("dat_besoin", shidat)
            if pd.isna(dat_besoin):
                dat_besoin = shidat
            dispo = _calc_stock_projete(stock, receptions_oa, receptions_of, comp_ref, dat_besoin)
        if dispo < qty_req:
            tous_disponibles = False
            composants_manquants.append({
                "composant": comp_ref,
                "requis": qty_req,
                "dispo": dispo,
                "manquant": qty_req - dispo,
            })
    motif = "" if tous_disponibles else f"{len(composants_manquants)} composant(s) manquant(s)"
    return {
        "satisfaisable": tous_disponibles,
        "of_existe": True,
        "composants_manquants": composants_manquants,
        "motif": motif,
    }


def calculer_satisfaction_s1(
    dfs: Dict[str, pd.DataFrame],
    date_ref: datetime,
) -> pd.DataFrame:
    commandes = dfs.get("commandes", pd.DataFrame())
    if commandes.empty:
        return pd.DataFrame()
    stock = dfs.get("stock", pd.DataFrame())
    of_entetes = dfs.get("of_entetes", pd.DataFrame())
    of_composants = dfs.get("of_composants", pd.DataFrame())
    receptions_oa = dfs.get("receptions_oa", pd.DataFrame())
    receptions_of = dfs.get("receptions_of", pd.DataFrame())
    cmd_s1 = filtrer_commandes_s1(commandes, date_ref)
    if cmd_s1.empty:
        return pd.DataFrame()
    resultats: List[dict] = []
    for _, row in cmd_s1.iterrows():
        is_mto = row.get("flag_contremarque", 0) == 1
        type_ligne = "mto" if is_mto else "sur_stock"
        if is_mto:
            res_imm = evaluer_ligne_mto(
                row, of_entetes, of_composants, stock, receptions_oa, receptions_of, "immediat"
            )
            res_proj = evaluer_ligne_mto(
                row, of_entetes, of_composants, stock, receptions_oa, receptions_of, "projete"
            )
        else:
            res_imm = evaluer_ligne_sur_stock(row, stock, receptions_oa, receptions_of, "immediat")
            res_proj = evaluer_ligne_sur_stock(row, stock, receptions_oa, receptions_of, "projete")
        resultats.append({
            "sohnum": row["sohnum"],
            "soplin": row["soplin"],
            "client_code": row["client_code"],
            "client_nom": row["client_nom"],
            "itmref": row["itmref"],
            "designation": row.get("designation", ""),
            "qte_restante": row["qte_restante"],
            "shidat": row["shidat"],
            "type_ligne": type_ligne,
            "satisfaisable_immediat": res_imm["satisfaisable"],
            "satisfaisable_projete": res_proj["satisfaisable"],
            "motif_imm": res_imm.get("motif", ""),
            "motif_proj": res_proj.get("motif", ""),
        })
    return pd.DataFrame(resultats)


def aggreg_par_client(df_resultats: pd.DataFrame) -> pd.DataFrame:
    if df_resultats.empty:
        return pd.DataFrame()
    agg = df_resultats.groupby(["client_code", "client_nom"], as_index=False).agg(
        nb_lignes_total=("sohnum", "count"),
        nb_satisfaisables_immediat=("satisfaisable_immediat", "sum"),
        nb_satisfaisables_projete=("satisfaisable_projete", "sum"),
    )
    agg["taux_immediat_pct"] = round(agg["nb_satisfaisables_immediat"] / agg["nb_lignes_total"] * 100, 1)
    agg["taux_projete_pct"] = round(agg["nb_satisfaisables_projete"] / agg["nb_lignes_total"] * 100, 1)
    agg = agg.sort_values("taux_immediat_pct", ascending=True)
    return agg


def resume_global(df_resultats: pd.DataFrame) -> dict:
    if df_resultats.empty:
        return {
            "nb_lignes_total": 0,
            "nb_satisfaisables_immediat": 0,
            "nb_satisfaisables_projete": 0,
            "taux_immediat_pct": 0.0,
            "taux_projete_pct": 0.0,
        }
    total = len(df_resultats)
    imm = int(df_resultats["satisfaisable_immediat"].sum())
    proj = int(df_resultats["satisfaisable_projete"].sum())
    return {
        "nb_lignes_total": total,
        "nb_satisfaisables_immediat": imm,
        "nb_satisfaisables_projete": proj,
        "taux_immediat_pct": round(imm / total * 100, 1),
        "taux_projete_pct": round(proj / total * 100, 1),
    }
