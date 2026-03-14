from __future__ import annotations

from datetime import timedelta
from typing import Dict, Set

import pandas as pd

from .calendar import FERMETURES_AERECO, soustraire_jours_ouvres
from .config import PREFIXES_NON_BLOQUANTS


def preparer_donnees(dfs: Dict[str, pd.DataFrame], params: dict) -> Dict[str, pd.DataFrame]:
    date_ref = params["date_reference"]
    date_lim = date_ref + timedelta(days=params["horizon_jours"])
    cmd = dfs["commandes"].copy()
    cmd = cmd[cmd["shidat"].notna() & (cmd["shidat"] <= date_lim) & (cmd["qte_restante"] > 0)].copy()
    cmd["date_besoin_prod"] = cmd["shidat"].apply(
        lambda d: soustraire_jours_ouvres(d, params["jours_ouvres_avant_expedition"], FERMETURES_AERECO)
    )
    cmd["type_flux"] = cmd.apply(
        lambda r: "contremarque"
        if r["flag_contremarque"] == 1
        and str(r["mfgnum_lie"]).strip() not in ["", "nan", "None", "NaN", "0", "0.0"]
        else "sur_stock",
        axis=1,
    )
    cmd = cmd.sort_values("shidat").reset_index(drop=True)
    dfs["commandes"] = cmd

    stk = dfs["stock"].copy()
    if params["deduire_allocations"]:
        stk["dispo_instantane"] = (
            stk["stock_physique"] - stk["stock_alloue"] - stk["stock_bloque"]
        ).clip(lower=0)
    else:
        stk["dispo_instantane"] = stk["stock_physique"]
    dfs["stock"] = stk

    from .feasibility import preparer_composants
    dfs = preparer_composants(dfs)

    from .config import STATUTS_NON_AFFERMIS, STATUTS_FERMES_LANCES
    of_ent = dfs["of_entetes"].copy()
    dfs["of_non_affermis"] = of_ent[of_ent["mfgsta"].isin(STATUTS_NON_AFFERMIS)].copy()
    dfs["of_fermes_lances"] = of_ent[of_ent["mfgsta"].isin(STATUTS_FERMES_LANCES)].copy()
    return dfs


def allouer_stock_commandes(dfs: Dict[str, pd.DataFrame]) -> pd.DataFrame:
    cmd = dfs["commandes"].copy()
    stock_dispo: Dict[str, float] = dfs["stock"].set_index("itmref")["dispo_instantane"].to_dict()
    cmd["couverture_stock"] = 0.0
    cmd["ecart_a_couvrir"] = 0.0
    cmd["of_associe"] = ""
    cmd["source_couverture"] = ""
    for idx in cmd.index:
        row = cmd.loc[idx]
        if row["type_flux"] == "contremarque":
            cmd.at[idx, "of_associe"] = str(row["mfgnum_lie"]).strip()
            cmd.at[idx, "source_couverture"] = "contremarque"
            cmd.at[idx, "ecart_a_couvrir"] = row["qte_restante"]
            continue
        art = row["itmref"]
        besoin = row["qte_restante"]
        dispo = stock_dispo.get(art, 0.0)
        if dispo >= besoin:
            cmd.at[idx, "couverture_stock"] = besoin
            cmd.at[idx, "ecart_a_couvrir"] = 0.0
            cmd.at[idx, "source_couverture"] = "stock"
            stock_dispo[art] = dispo - besoin
        elif dispo > 0:
            cmd.at[idx, "couverture_stock"] = dispo
            cmd.at[idx, "ecart_a_couvrir"] = besoin - dispo
            cmd.at[idx, "source_couverture"] = "stock_partiel"
            stock_dispo[art] = 0.0
        else:
            cmd.at[idx, "ecart_a_couvrir"] = besoin
            cmd.at[idx, "source_couverture"] = "aucun_stock"
    dfs["commandes"] = cmd
    return cmd


def apparier_commandes_of(dfs: Dict[str, pd.DataFrame]) -> pd.DataFrame:
    cmd = dfs["commandes"].copy()
    of_all = dfs["of_entetes"].copy()
    of_candidats = of_all[of_all["qte_restante"] > 0].copy()
    of_attribues: Set[str] = set(
        cmd[cmd["type_flux"] == "contremarque"]["of_associe"].dropna().unique()
    )
    for idx in cmd.index:
        row = cmd.loc[idx]
        if row["type_flux"] == "contremarque" or row["ecart_a_couvrir"] <= 0:
            continue
        candidats = of_candidats[
            (of_candidats["itmref"] == row["itmref"])
            & (~of_candidats["mfgnum"].isin(of_attribues))
        ].copy()
        if candidats.empty:
            lbl = (
                "stock_partiel+aucun_of"
                if cmd.at[idx, "source_couverture"] == "stock_partiel"
                else "aucun_approvisionnement"
            )
            cmd.at[idx, "source_couverture"] = lbl
            continue
        date_besoin = row["date_besoin_prod"]
        if pd.notna(date_besoin):
            candidats_a_date = candidats[
                candidats["enddat"].notna() & (candidats["enddat"] <= date_besoin)
            ].copy()
            if candidats_a_date.empty:
                lbl = (
                    "stock_partiel+aucun_of_a_date"
                    if cmd.at[idx, "source_couverture"] == "stock_partiel"
                    else "aucun_of_a_date"
                )
                cmd.at[idx, "source_couverture"] = lbl
                continue
            candidats_a_date["ecart_date"] = (date_besoin - candidats_a_date["enddat"]).dt.days
            meilleur = candidats_a_date.sort_values(["ecart_date", "enddat"]).iloc[0]
        else:
            meilleur = candidats.iloc[0]
        cmd.at[idx, "of_associe"] = meilleur["mfgnum"]
        of_attribues.add(meilleur["mfgnum"])
        lbl = "stock_partiel+of" if cmd.at[idx, "source_couverture"] == "stock_partiel" else "of"
        cmd.at[idx, "source_couverture"] = lbl
    dfs["commandes"] = cmd
    return cmd
