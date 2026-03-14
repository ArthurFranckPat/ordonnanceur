from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Optional, Set, Tuple

import numpy as np
import pandas as pd

from .calendar import FERMETURES_AERECO, to_python_datetime
from .config import PREFIXES_NON_BLOQUANTS, STATUTS_FERMES_LANCES, STATUTS_NON_AFFERMIS


def _calc_dispo_proj(
    article: str,
    date_besoin: object,
    dfs: Dict[str, pd.DataFrame],
    stock_w: Dict[str, float],
) -> float:
    dispo = stock_w.get(article, 0.0)
    if pd.isna(date_besoin):  # type: ignore[arg-type]
        return dispo
    oa = dfs["receptions_oa"]
    dispo += float(
        oa[(oa["itmref"] == article) & (oa["date_reception"].notna()) & (oa["date_reception"] <= date_besoin)][
            "qte_restante"
        ].sum()
    )
    rof = dfs["receptions_of"]
    dispo += float(
        rof[(rof["itmref"] == article) & (rof["enddat"].notna()) & (rof["enddat"] <= date_besoin)][
            "qte_restante"
        ].sum()
    )
    return dispo


def _calc_date_couverture(
    article: str,
    qty_requise: float,
    dfs: Dict[str, pd.DataFrame],
    stock_w: Dict[str, float],
) -> Optional[pd.Timestamp]:
    dispo = stock_w.get(article, 0.0)
    if dispo >= qty_requise:
        return None
    receptions: List[Tuple[pd.Timestamp, float]] = []
    oa = dfs["receptions_oa"]
    for _, r in oa[(oa["itmref"] == article) & (oa["qte_restante"] > 0) & (oa["date_reception"].notna())].iterrows():
        receptions.append((r["date_reception"], r["qte_restante"]))
    rof = dfs["receptions_of"]
    for _, r in rof[(rof["itmref"] == article) & (rof["qte_restante"] > 0) & (rof["enddat"].notna())].iterrows():
        receptions.append((r["enddat"], r["qte_restante"]))
    receptions.sort(key=lambda x: x[0])
    cumul = dispo
    for dt, qt in receptions:
        cumul += qt
        if cumul >= qty_requise:
            return dt
    return None


def _trouver_of_pour_article(
    article: str, dfs: Dict[str, pd.DataFrame], of_deja_traites: Set[str]
) -> Optional[str]:
    of_all = dfs["of_entetes"]
    candidats = of_all[(of_all["itmref"] == article) & (~of_all["mfgnum"].isin(of_deja_traites))].copy()
    if candidats.empty:
        return None
    na = candidats[candidats["mfgsta"].isin(STATUTS_NON_AFFERMIS)]
    if not na.empty:
        return str(na.iloc[0]["mfgnum"])
    fl = candidats[candidats["mfgsta"].isin(STATUTS_FERMES_LANCES)]
    if not fl.empty:
        return str(fl.iloc[0]["mfgnum"])
    return str(candidats.iloc[0]["mfgnum"])


def eval_of(
    mfgnum: str,
    dfs: Dict[str, pd.DataFrame],
    stock_w: Dict[str, float],
    of_deja_traites: Optional[Set[str]] = None,
    profondeur: int = 0,
    max_prof: int = 10,
) -> dict:
    if of_deja_traites is None:
        of_deja_traites = set()
    empty_result: dict = {
        "mfgnum": mfgnum, "nb_composants": 0, "ratio_instantane": 0.0,
        "ratio_projete": 0.0, "feu": "ROUGE", "detail_composants": [],
        "nb_manquants_instant": 0, "date_premiere_faisabilite": None,
        "lancable": False, "of_enfants": [],
    }
    if profondeur > max_prof:
        return empty_result

    comp = dfs["of_composants"]
    c_of = comp[comp["mfgnum"] == mfgnum].copy()
    of_info = dfs["of_entetes"][dfs["of_entetes"]["mfgnum"] == mfgnum]
    if c_of.empty or of_info.empty:
        return empty_result

    of_row = of_info.iloc[0]
    date_besoin = of_row["enddat"]
    details: list = []
    ni = 0; np_ = 0; nt = len(c_of); nm_instant = 0
    ni_bloq = 0; nt_bloq = 0; np_bloq = 0
    date_fais_max = None
    of_enfants_resultats: list = []
    of_deja_traites.add(mfgnum)

    for _, c in c_of.iterrows():
        art = c["composant"]
        qr = c["qty_requise"]
        non_bloq = c["est_non_bloquant"]
        di = stock_w.get(art, 0.0)
        dp = _calc_dispo_proj(art, date_besoin, dfs, stock_w)
        ci = di >= qr
        cp = dp >= qr

        if ci:
            feu_comp = "VERT"
        elif cp:
            feu_comp = "ORANGE"
            nm_instant += 1
        else:
            feu_comp = "ROUGE"
            nm_instant += 1
        if ci:
            ni += 1
        if cp:
            np_ += 1
        if not non_bloq:
            nt_bloq += 1
            if ci:
                ni_bloq += 1
            if cp:
                np_bloq += 1

        dc = _calc_date_couverture(art, qr, dfs, stock_w) if not ci else None
        if dc is not None:
            dc_ts = pd.Timestamp(dc)
            if date_fais_max is None or dc_ts > pd.Timestamp(date_fais_max):
                date_fais_max = dc
        ecart_instant = max(0.0, qr - di)

        prd = None; prs = ""
        oa = dfs["receptions_oa"]
        oa_a = oa[(oa["itmref"] == art) & (oa["qte_restante"] > 0) & (oa["date_reception"].notna())]
        if not oa_a.empty:
            bo = oa_a.sort_values("date_reception").iloc[0]
            prd = bo["date_reception"]
            prs = f"OA {bo['pohnum']}"
        rof_df = dfs["receptions_of"]
        rf_a = rof_df[(rof_df["itmref"] == art) & (rof_df["qte_restante"] > 0) & (rof_df["enddat"].notna())]
        if not rf_a.empty:
            br = rf_a.sort_values("enddat").iloc[0]
            if prd is None or br["enddat"] < prd:
                prd = br["enddat"]
                prs = f"OF {br['mfgnum']}"

        of_enfant_num = None; date_dispo_sf = None; retard_sf = None
        if non_bloq and not ci:
            of_enfant_num = _trouver_of_pour_article(art, dfs, of_deja_traites)
            if of_enfant_num:
                of_enfant_result = eval_of(of_enfant_num, dfs, stock_w, of_deja_traites, profondeur + 1)
                of_enfants_resultats.append(of_enfant_result)
                of_enfant_info = dfs["of_entetes"][dfs["of_entetes"]["mfgnum"] == of_enfant_num]
                enddat_enfant = of_enfant_info.iloc[0]["enddat"] if not of_enfant_info.empty else None
                df_of_enfant = of_enfant_result["date_premiere_faisabilite"]
                dates_c = [
                    x for x in [enddat_enfant, df_of_enfant]
                    if x is not None and not (isinstance(x, float) and np.isnan(x))
                ]
                if dates_c:
                    date_dispo_sf = max(dates_c)
                    dat_besoin_comp = c["dat_besoin"]
                    if pd.notna(dat_besoin_comp) and pd.notna(date_dispo_sf):
                        d1 = to_python_datetime(date_dispo_sf)
                        d2 = to_python_datetime(dat_besoin_comp)
                        if d1 is not None and d2 is not None:
                            delta = (d1 - d2).days
                            retard_sf = delta if delta > 0 else 0

        details.append({
            "mfgnum": mfgnum, "composant": art, "designation": c["designation"],
            "categorie": c.get("categorie", ""), "bloquant": "Oui" if c["flag_bloquant"] else "Non",
            "qty_requise": qr, "dispo_instantane": di, "dispo_projete": round(dp, 2),
            "ecart_instant": ecart_instant, "feu_composant": feu_comp, "date_couverture": dc,
            "date_prochaine_reception": prd, "source_reception": prs,
            "of_enfant": of_enfant_num, "date_dispo_sf": date_dispo_sf, "retard_sf_jours": retard_sf,
        })

    ri = round(ni / nt * 100, 1) if nt else 0.0
    rp = round(np_ / nt * 100, 1) if nt else 0.0
    if rp == 100:
        feu = "VERT" if ri == 100 else "ORANGE"
    elif rp > 0:
        feu = "ORANGE"
    else:
        feu = "ROUGE"
    lancable = (np_bloq == nt_bloq) if nt_bloq > 0 else True

    return {
        "mfgnum": mfgnum, "nb_composants": nt, "ratio_instantane": ri, "ratio_projete": rp,
        "feu": feu, "detail_composants": details, "nb_manquants_instant": nm_instant,
        "date_premiere_faisabilite": date_fais_max, "lancable": lancable,
        "of_enfants": of_enfants_resultats,
    }


def preparer_composants(dfs: Dict[str, pd.DataFrame]) -> Dict[str, pd.DataFrame]:
    comp = dfs["of_composants"].copy()
    articles = dfs["articles"][["itmref", "type_appro", "categorie"]].copy()
    comp = comp.merge(articles.rename(columns={"itmref": "composant"}), on="composant", how="left")
    comp["est_non_bloquant"] = comp["categorie"].apply(
        lambda c: any(str(c).upper().startswith(p) for p in PREFIXES_NON_BLOQUANTS)
        if pd.notna(c) and str(c).strip() not in ["", "nan"]
        else False
    )
    comp["flag_bloquant"] = ~comp["est_non_bloquant"]
    dfs["of_composants"] = comp
    return dfs


def alloc_seq_composants(
    of_list: List[str], dfs: Dict[str, pd.DataFrame], params: dict
) -> Dict[str, dict]:
    stock_w: Dict[str, float] = dfs["stock"].set_index("itmref")["dispo_instantane"].to_dict()
    alloc = params["allocation_sequentielle"]
    rb: Dict[str, float] = {}
    for m in of_list:
        rb[m] = eval_of(m, dfs, stock_w.copy())["ratio_projete"]
    of_ent = dfs["of_entetes"].set_index("mfgnum")
    of_tries = sorted(
        of_list,
        key=lambda m: (
            -rb.get(m, 0),
            of_ent.loc[m, "enddat"] if m in of_ent.index else pd.Timestamp.max,
        ),
    )
    sa = stock_w.copy() if alloc else stock_w
    res: Dict[str, dict] = {}
    for m in of_tries:
        sw = sa.copy() if alloc else stock_w.copy()
        r = eval_of(m, dfs, sw, of_deja_traites=set())
        if alloc:
            comp = dfs["of_composants"]
            cs = comp[comp["mfgnum"] == m]
            for _, c in cs.iterrows():
                if c["flag_bloquant"]:
                    a = c["composant"]; q = c["qty_requise"]; d = sa.get(a, 0.0)
                    if d >= q:
                        sa[a] = d - q
            for enf in r.get("of_enfants", []):
                comp_enf = comp[comp["mfgnum"] == enf["mfgnum"]]
                for _, ce in comp_enf.iterrows():
                    if ce["flag_bloquant"]:
                        a = ce["composant"]; q = ce["qty_requise"]; d = sa.get(a, 0.0)
                        if d >= q:
                            sa[a] = d - q
        res[m] = r
    return res
