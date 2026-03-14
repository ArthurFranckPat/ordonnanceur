from __future__ import annotations

from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from .calendar import to_python_datetime
from .feasibility import eval_of


def _feu_capa_of(mfgnum: str, of_capa: Dict[str, dict]) -> Optional[str]:
    info = of_capa.get(mfgnum, {})
    if not info or info.get("alerte"):
        return None
    t = info.get("taux_moyen", 0)
    if t > 100:
        return "ROUGE"
    elif t >= 80:
        return "ORANGE"
    return "VERT"


def _pire_feu(f1: Optional[str], f2: Optional[str]) -> Optional[str]:
    ordre = {"ROUGE": 0, "ORANGE": 1, "VERT": 2}
    if f1 is None:
        return f2
    if f2 is None:
        return f1
    return f1 if ordre.get(f1, 2) <= ordre.get(f2, 2) else f2


def _collecter_enfants_inline(
    result: dict, dfs: Dict[str, pd.DataFrame], profondeur: int = 0
) -> List[dict]:
    rows: List[dict] = []
    for enf in result.get("of_enfants", []):
        of_info = dfs["of_entetes"][dfs["of_entetes"]["mfgnum"] == enf["mfgnum"]]
        if not of_info.empty:
            oi = of_info.iloc[0]
            article = oi["itmref"]
            designation = oi["designation"]
            date_fin = oi["enddat"]
            qte_fab = oi["extqty"]
            qte_prod = oi["cplqty"]
        else:
            article = ""; designation = ""; date_fin = pd.NaT; qte_fab = 0; qte_prod = 0
        prefix = ">>" * (profondeur + 1) + " "
        rows.append({
            "article": article, "designation": prefix + designation,
            "qte_fab": qte_fab, "qte_prod": qte_prod, "date_fin": date_fin,
            "date_faisabilite": enf["date_premiere_faisabilite"],
            "type_flux": "sous-ensemble",
            "ratio_instant": enf["ratio_instantane"], "ratio_projete": enf["ratio_projete"],
            "feu": enf["feu"], "nb_manquants": enf["nb_manquants_instant"],
            "lancable": "OUI" if enf["lancable"] else "NON", "mfgnum": enf["mfgnum"],
        })
        rows.extend(_collecter_enfants_inline(enf, dfs, profondeur + 1))
    return rows


def assembler(
    dfs: Dict[str, pd.DataFrame],
    resultats_of: Dict[str, dict],
    of_capa: Optional[Dict[str, dict]] = None,
) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    if of_capa is None:
        of_capa = {}
    cmd = dfs["commandes"].copy()
    cmd["ratio_instant_of"] = np.nan
    cmd["ratio_projete_of"] = np.nan
    cmd["feu_of"] = None
    cmd["nb_manquants_of"] = np.nan
    cmd["feu_matiere"] = None
    cmd["alerte"] = None
    cmd["date_faisabilite"] = pd.NaT
    cmd["lancable"] = None
    cmd["nb_of_enfants"] = 0
    cmd["poste_charge"] = None
    cmd["feu_capacite"] = None
    cmd["feu_ligne"] = None

    of_dates = dfs["of_entetes"][["mfgnum", "enddat"]].drop_duplicates(subset="mfgnum")
    of_dates_idx = of_dates.set_index("mfgnum") if not of_dates.empty else pd.DataFrame()

    for idx in cmd.index:
        row = cmd.loc[idx]
        on = str(row["of_associe"]).strip()
        date_besoin_prod = row.get("date_besoin_prod", pd.NaT)

        if row["source_couverture"] == "stock":
            cmd.at[idx, "feu_matiere"] = "VERT"
            cmd.at[idx, "feu_ligne"] = "VERT"
            cmd.at[idx, "lancable"] = "-"
            continue

        if "aucun" in str(row["source_couverture"]):
            cmd.at[idx, "feu_matiere"] = "ROUGE"
            cmd.at[idx, "feu_ligne"] = "ROUGE"
            cmd.at[idx, "alerte"] = "Aucun OF trouve"
            cmd.at[idx, "lancable"] = "-"
            continue

        if on in resultats_of:
            r = resultats_of[on]
            cmd.at[idx, "ratio_instant_of"] = r["ratio_instantane"]
            cmd.at[idx, "ratio_projete_of"] = r["ratio_projete"]
            cmd.at[idx, "feu_of"] = r["feu"]
            cmd.at[idx, "nb_manquants_of"] = r["nb_manquants_instant"]
            cmd.at[idx, "feu_matiere"] = r["feu"]
            cmd.at[idx, "lancable"] = "OUI" if r["lancable"] else "NON"
            if r["date_premiere_faisabilite"] is not None:
                cmd.at[idx, "date_faisabilite"] = r["date_premiere_faisabilite"]
            cmd.at[idx, "nb_of_enfants"] = len(r.get("of_enfants", []))
        elif on and on in dfs["of_fermes_lances"]["mfgnum"].values:
            r_ferme = eval_of(
                on, dfs, dfs["stock"].set_index("itmref")["dispo_instantane"].to_dict()
            )
            cmd.at[idx, "ratio_instant_of"] = r_ferme["ratio_instantane"]
            cmd.at[idx, "ratio_projete_of"] = r_ferme["ratio_projete"]
            cmd.at[idx, "feu_of"] = r_ferme["feu"]
            cmd.at[idx, "nb_manquants_of"] = r_ferme["nb_manquants_instant"]
            cmd.at[idx, "feu_matiere"] = r_ferme["feu"]
            cmd.at[idx, "lancable"] = "FERME"
            if r_ferme["date_premiere_faisabilite"] is not None:
                cmd.at[idx, "date_faisabilite"] = r_ferme["date_premiere_faisabilite"]
            if r_ferme["nb_manquants_instant"] > 0:
                resultats_of[on] = r_ferme
        else:
            cmd.at[idx, "feu_matiere"] = "ROUGE"
            cmd.at[idx, "feu_ligne"] = "ROUGE"
            cmd.at[idx, "alerte"] = f"OF {on} introuvable"
            cmd.at[idx, "lancable"] = "-"
            continue

        if on and not of_dates_idx.empty and on in of_dates_idx.index and pd.notna(date_besoin_prod):
            date_fin_of = of_dates_idx.loc[on, "enddat"]
            date_fin_of_dt = to_python_datetime(date_fin_of)
            date_besoin_prod_dt = to_python_datetime(date_besoin_prod)
            if (
                date_fin_of_dt is not None
                and date_besoin_prod_dt is not None
                and date_fin_of_dt > date_besoin_prod_dt
            ):
                retard_j = (date_fin_of_dt - date_besoin_prod_dt).days
                alerte_retard = f"OF apres besoin commande (+{retard_j}j)"
                alerte_existante = cmd.at[idx, "alerte"]
                cmd.at[idx, "alerte"] = (
                    alerte_retard
                    if pd.isna(alerte_existante) or not alerte_existante
                    else f"{alerte_existante} | {alerte_retard}"
                )
                if row["type_flux"] == "contremarque":
                    cmd.at[idx, "feu_ligne"] = "ROUGE"

        fc = _feu_capa_of(on, of_capa)
        capa_info = of_capa.get(on, {})
        cmd.at[idx, "poste_charge"] = capa_info.get("poste", "")
        cmd.at[idx, "feu_capacite"] = fc
        if capa_info.get("alerte"):
            cmd.at[idx, "alerte"] = capa_info["alerte"]
        cmd.at[idx, "feu_ligne"] = _pire_feu(cmd.at[idx, "feu_matiere"], fc)

    colonnes = [
        "N Commande", "Ligne", "Code Client", "Nom Client", "Article", "Designation",
        "Qte Commandee", "Qte Restante", "Date Expedition", "Date Besoin Prod", "Type Flux",
        "Couvert par Stock", "Ecart a Couvrir", "OF Associe", "Source Couverture",
        "Ratio Instant OF (%)", "Ratio Projete OF (%)", "Feu Matiere", "Feu Capacite", "Feu Ligne",
        "Poste Charge", "Nb Manquants (instant)", "Lancable", "Date 1ere Faisabilite",
        "Nb OF Enfants", "Alerte",
    ]
    all_rows: List[dict] = []
    for idx in cmd.index:
        row = cmd.loc[idx]
        all_rows.append({
            "N Commande": row["sohnum"], "Ligne": row["soplin"],
            "Code Client": row["client_code"], "Nom Client": row["client_nom"],
            "Article": row["itmref"], "Designation": row["designation"],
            "Qte Commandee": row["qte_commandee"], "Qte Restante": row["qte_restante"],
            "Date Expedition": row["shidat"], "Date Besoin Prod": row["date_besoin_prod"],
            "Type Flux": row["type_flux"],
            "Couvert par Stock": row["couverture_stock"], "Ecart a Couvrir": row["ecart_a_couvrir"],
            "OF Associe": row["of_associe"], "Source Couverture": row["source_couverture"],
            "Ratio Instant OF (%)": row["ratio_instant_of"],
            "Ratio Projete OF (%)": row["ratio_projete_of"],
            "Feu Matiere": row["feu_matiere"], "Feu Capacite": row["feu_capacite"],
            "Feu Ligne": row["feu_ligne"],
            "Poste Charge": row["poste_charge"],
            "Nb Manquants (instant)": row["nb_manquants_of"],
            "Lancable": row["lancable"], "Date 1ere Faisabilite": row["date_faisabilite"],
            "Nb OF Enfants": row["nb_of_enfants"], "Alerte": row["alerte"],
        })
        on = str(row["of_associe"]).strip()
        if on in resultats_of:
            enfants = _collecter_enfants_inline(resultats_of[on], dfs)
            for enf in enfants:
                enf_capa = of_capa.get(enf["mfgnum"], {})
                all_rows.append({
                    "N Commande": "", "Ligne": "", "Code Client": "", "Nom Client": "",
                    "Article": enf["article"], "Designation": enf["designation"],
                    "Qte Commandee": enf["qte_fab"], "Qte Restante": enf["qte_prod"],
                    "Date Expedition": enf["date_fin"], "Date Besoin Prod": "",
                    "Type Flux": "sous-ensemble", "Couvert par Stock": "", "Ecart a Couvrir": "",
                    "OF Associe": enf["mfgnum"], "Source Couverture": "",
                    "Ratio Instant OF (%)": enf["ratio_instant"],
                    "Ratio Projete OF (%)": enf["ratio_projete"],
                    "Feu Matiere": enf["feu"], "Feu Capacite": _feu_capa_of(enf["mfgnum"], of_capa),
                    "Feu Ligne": "",
                    "Poste Charge": enf_capa.get("poste", ""),
                    "Nb Manquants (instant)": enf["nb_manquants"],
                    "Lancable": enf["lancable"], "Date 1ere Faisabilite": enf["date_faisabilite"],
                    "Nb OF Enfants": "", "Alerte": enf_capa.get("alerte", ""),
                })
    df_cmd = pd.DataFrame(all_rows, columns=colonnes)

    dr: List[dict] = []
    for on, r in resultats_of.items():
        cl = cmd[cmd["of_associe"] == on]
        soh = cl["sohnum"].iloc[0] if not cl.empty else ""
        date_besoin_prod = cl["date_besoin_prod"].iloc[0] if not cl.empty else pd.NaT
        for d in r["detail_composants"]:
            if d["dispo_instantane"] >= d["qty_requise"]:
                continue
            retard_flag = ""; retard_j = None
            if pd.notna(d["date_prochaine_reception"]) and pd.notna(date_besoin_prod):
                dr_ = to_python_datetime(d["date_prochaine_reception"])
                dbp = to_python_datetime(date_besoin_prod)
                if dr_ is not None and dbp is not None:
                    delta = (dr_ - dbp).days
                    if delta > 0:
                        retard_j = delta; retard_flag = f"RETARD {delta}j"
                    else:
                        retard_flag = "OK"
            elif d["date_prochaine_reception"] is None or pd.isna(d.get("date_prochaine_reception")):
                retard_flag = "AUCUNE RECEPTION"
            of_enf_info = ""
            if d.get("of_enfant"):
                of_enf_info = d["of_enfant"]
                if d.get("retard_sf_jours") and d["retard_sf_jours"] > 0:
                    of_enf_info += f" (RETARD {d['retard_sf_jours']}j)"
                elif d.get("date_dispo_sf"):
                    of_enf_info += " (OK)"
            dr.append({
                "N Commande": soh, "MFGNUM": on,
                "Composant": d["composant"], "Designation": d["designation"],
                "Categorie": d["categorie"], "Bloquant": d["bloquant"],
                "Qte Requise": d["qty_requise"], "Dispo Instantane": d["dispo_instantane"],
                "Dispo Projete": d["dispo_projete"], "Ecart (instant)": d["ecart_instant"],
                "Feu": d["feu_composant"],
                "Date Proch Reception": d["date_prochaine_reception"],
                "Source Reception": d["source_reception"],
                "Date Couverture": d["date_couverture"],
                "Impact Livraison": retard_flag, "Retard (jours)": retard_j,
                "OF Enfant (SF/PF)": of_enf_info, "Date Dispo SF": d.get("date_dispo_sf"),
            })
    df_det = pd.DataFrame(dr) if dr else pd.DataFrame()

    ad: List[dict] = []
    for on, r in resultats_of.items():
        for d in r["detail_composants"]:
            if d["bloquant"] == "Oui":
                ad.append(d)
        for enf in r.get("of_enfants", []):
            for d in enf["detail_composants"]:
                if d["bloquant"] == "Oui":
                    ad.append(d)
    if ad:
        da = pd.DataFrame(ad)
        cr = da.groupby("composant").agg(
            designation=("designation", "first"),
            categorie=("categorie", "first"),
            dispo_instantane=("dispo_instantane", "first"),
            besoin_total=("qty_requise", "sum"),
            nb_of=("mfgnum", "nunique"),
            of_list=("mfgnum", lambda x: ", ".join(sorted(x.unique()))),
        ).reset_index()
        cr["taux_couv"] = (
            cr["dispo_instantane"] / cr["besoin_total"].replace(0, np.nan) * 100
        ).round(1)

        def cnt_cmd(ol: str) -> int:
            return len(
                set(
                    s
                    for o in ol.split(", ")
                    for s in cmd[cmd["of_associe"] == o]["sohnum"].unique()
                )
            )

        cr["nb_cmd"] = cr["of_list"].apply(cnt_cmd)
        cr = cr.sort_values("taux_couv").reset_index(drop=True)
        cr.columns = [
            "Composant", "Designation", "Categorie", "Stock Dispo", "Besoin Total",
            "Nb OF", "OF Concernes", "Taux Couverture (%)", "Nb Commandes",
        ]
    else:
        cr = pd.DataFrame()
    return df_cmd, df_det, cr
