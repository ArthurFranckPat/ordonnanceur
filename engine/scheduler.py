from __future__ import annotations

from datetime import timedelta
from typing import Dict, List, Set, Tuple

import pandas as pd

from .calendar import (
    FERMETURES_AERECO,
    dernier_jour_ouvre_avant_ou_egal,
    generer_jours_ouvres,
    jour_ouvre_precedent,
)


def calculer_plan_charge(
    dfs: Dict[str, pd.DataFrame],
    resultats_of: Dict[str, dict],
    params: dict,
) -> Tuple[pd.DataFrame, Dict[str, dict], pd.DataFrame]:
    gammes = dfs["gammes"]
    of_ent = dfs["of_entetes"]
    date_ref = params["date_reference"]
    horizon = params["horizon_jours"]
    capa_jour = params["capacite_heures_semaine"] / 5.0
    perimetre = params["perimetre_chargement"]

    of_a_charger: Set[str]
    if perimetre == "commandes":
        of_a_charger = set()
        for on, r in resultats_of.items():
            of_a_charger.add(on)
            for enf in r.get("of_enfants", []):
                of_a_charger.add(enf["mfgnum"])
    else:
        of_a_charger = set(of_ent[of_ent["qte_restante"] > 0]["mfgnum"].values)

    gamme_idx = gammes.drop_duplicates(subset="itmref").set_index("itmref")

    date_lim = date_ref + timedelta(days=horizon)
    jours_ouvres_horizon = generer_jours_ouvres(date_ref, date_lim, FERMETURES_AERECO)
    if not jours_ouvres_horizon:
        return pd.DataFrame(), {}, pd.DataFrame()

    charge_map: Dict[str, Dict] = {}
    of_capa: Dict[str, dict] = {}

    for mfgnum in of_a_charger:
        of_info = of_ent[of_ent["mfgnum"] == mfgnum]
        if of_info.empty:
            continue
        oi = of_info.iloc[0]
        article = oi["itmref"]
        qte = oi["qte_restante"]
        if qte <= 0:
            continue

        if article not in gamme_idx.index:
            of_capa[mfgnum] = {
                "poste": "", "libelle_poste": "", "charge_h": 0,
                "date_debut": None, "date_fin": None, "alerte": "Gamme manquante",
            }
            continue
        gam = gamme_idx.loc[article]
        poste = gam["poste_charge"]
        libelle = gam["libelle_poste"]
        cadence = gam["cadence"]
        if cadence <= 0:
            of_capa[mfgnum] = {
                "poste": poste, "libelle_poste": libelle, "charge_h": 0,
                "date_debut": None, "date_fin": None, "alerte": "Cadence nulle",
            }
            continue

        charge_h = qte / cadence
        date_fin_of = oi["enddat"]
        if pd.isna(date_fin_of):
            date_fin_of = date_ref

        if poste not in charge_map:
            charge_map[poste] = {}

        jour = dernier_jour_ouvre_avant_ou_egal(date_fin_of, FERMETURES_AERECO)
        charge_restante = charge_h
        date_debut_charge = None
        date_fin_charge = None
        while charge_restante > 0:
            if jour not in charge_map[poste]:
                charge_map[poste][jour] = {"charge": 0.0, "of_list": [], "allocations": []}
            capa_restante = max(0.0, capa_jour - charge_map[poste][jour]["charge"])
            if capa_restante > 0:
                charge_affectee = min(charge_restante, capa_restante)
                charge_map[poste][jour]["charge"] += charge_affectee
                charge_map[poste][jour]["of_list"].append(mfgnum)
                charge_map[poste][jour]["allocations"].append({"mfgnum": mfgnum, "charge_h": charge_affectee})
                charge_restante -= charge_affectee
                date_debut_charge = jour
                if date_fin_charge is None:
                    date_fin_charge = jour
            if charge_restante > 0:
                jour = jour_ouvre_precedent(jour, FERMETURES_AERECO)

        of_capa[mfgnum] = {
            "poste": poste, "libelle_poste": libelle, "charge_h": round(charge_h, 2),
            "date_debut": date_debut_charge, "date_fin": date_fin_charge, "alerte": "",
        }

    jours_affiches: Set = set(jours_ouvres_horizon)
    for poste_map in charge_map.values():
        jours_affiches.update(poste_map.keys())
    jours_ouvres = sorted(jours_affiches)

    capa_sem = params["capacite_heures_semaine"]
    postes_tous = sorted(charge_map.keys())

    semaine_charge: Dict[Tuple, Dict] = {}
    for jour in jours_ouvres:
        iso = jour.isocalendar()
        sem_key = (iso[0], iso[1])
        if sem_key not in semaine_charge:
            semaine_charge[sem_key] = {}
        for poste in postes_tous:
            if poste not in semaine_charge[sem_key]:
                semaine_charge[sem_key][poste] = {"charge": 0.0, "of_set": set()}
            ch = charge_map.get(poste, {}).get(jour, {}).get("charge", 0.0)
            ofs = charge_map.get(poste, {}).get(jour, {}).get("of_list", [])
            semaine_charge[sem_key][poste]["charge"] += ch
            semaine_charge[sem_key][poste]["of_set"].update(ofs)

    rows = []
    for sem_key in sorted(semaine_charge.keys()):
        annee, num_sem = sem_key
        row: dict = {"Semaine": f"S{num_sem:02d}-{annee}", "Capa (h/sem)": capa_sem}
        for poste in postes_tous:
            info_sem = semaine_charge[sem_key].get(poste, {"charge": 0.0, "of_set": set()})
            ch = info_sem["charge"]
            taux = round(ch / capa_sem * 100, 1) if capa_sem > 0 else 0.0
            nb_of = len(info_sem["of_set"])
            row[f"{poste} (h)"] = round(ch, 1)
            row[f"{poste} (%)"] = taux
            row[f"{poste} (OF)"] = nb_of
        rows.append(row)
    df_plan = pd.DataFrame(rows)

    for mfgnum, info in of_capa.items():
        if info["date_debut"] and info["date_fin"] and info["poste"]:
            poste = info["poste"]
            d1 = info["date_debut"]
            d2 = info["date_fin"]
            jours_of = generer_jours_ouvres(d1, d2, FERMETURES_AERECO) if d1 <= d2 else []
            semaines_of: Set[Tuple] = set()
            for j in jours_of:
                iso = j.isocalendar()
                semaines_of.add((iso[0], iso[1]))
            if semaines_of:
                taux_list = []
                for sk in semaines_of:
                    ch_sem = semaine_charge.get(sk, {}).get(poste, {}).get("charge", 0.0)
                    taux_list.append(ch_sem / capa_sem * 100 if capa_sem > 0 else 0.0)
                info["taux_moyen"] = round(sum(taux_list) / len(taux_list), 1)
            else:
                info["taux_moyen"] = 0.0
        else:
            info["taux_moyen"] = 0.0

    charge_segment_rows: List[dict] = []
    heure_debut_journee = 8.0
    for poste in postes_tous:
        for jour in jours_ouvres:
            allocs = charge_map.get(poste, {}).get(jour, {}).get("allocations", [])
            if not allocs:
                continue
            charge_totale_jour = sum(float(a["charge_h"]) for a in allocs)
            fenetre_visible_h = capa_jour if capa_jour > 0 else 14.0
            ratio_affichage = min(1.0, fenetre_visible_h / charge_totale_jour) if charge_totale_jour > 0 else 1.0
            cumul_h = 0.0
            for idx, alloc in enumerate(allocs):
                mfgnum = alloc["mfgnum"]
                charge_h = float(alloc["charge_h"])
                iso = jour.isocalendar()
                sem_label = f"S{iso[1]:02d}-{iso[0]}"
                charge_h_visible = charge_h * ratio_affichage
                segment_start = jour + timedelta(hours=heure_debut_journee + cumul_h)
                segment_end = jour + timedelta(hours=heure_debut_journee + cumul_h + charge_h_visible)
                cumul_h += charge_h_visible

                of_info_row = of_ent[of_ent["mfgnum"] == mfgnum]
                if of_info_row.empty:
                    article = ""; designation = ""; statut_num = None
                    statut_of = ""; date_fin_of = pd.NaT; date_debut_of = pd.NaT
                else:
                    oi = of_info_row.iloc[0]
                    article = oi["itmref"]
                    designation = oi["designation"]
                    statut_num = oi["mfgsta"]
                    statut_of = oi["mfgsta_lib"]
                    date_fin_of = oi["enddat"]
                    date_debut_of = of_capa.get(mfgnum, {}).get("date_debut", pd.NaT)

                charge_segment_rows.append({
                    "Semaine": sem_label,
                    "Date": jour,
                    "Poste": poste,
                    "MFGNUM": mfgnum,
                    "Article": article,
                    "Designation": designation,
                    "Statut OF": statut_of,
                    "Statut Num": statut_num,
                    "Date debut OF": date_debut_of,
                    "Date fin OF": date_fin_of,
                    "Charge (h)": round(charge_h, 2),
                    "Charge visible (h)": round(charge_h_visible, 2),
                    "Segment debut": segment_start,
                    "Segment fin": segment_end,
                    "Ordre jour": idx,
                })

    if charge_segment_rows:
        df_charge_segments = pd.DataFrame(charge_segment_rows)
        df_charge_segments = df_charge_segments.sort_values(
            ["Semaine", "Poste", "Date", "Ordre jour", "MFGNUM"]
        ).reset_index(drop=True)
        df_charge_segments = df_charge_segments.drop(columns=["Ordre jour"])
    else:
        df_charge_segments = pd.DataFrame(columns=[
            "Semaine", "Date", "Poste", "MFGNUM", "Article", "Designation",
            "Statut OF", "Statut Num", "Date debut OF", "Date fin OF",
            "Charge (h)", "Charge visible (h)", "Segment debut", "Segment fin",
        ])

    return df_plan, of_capa, df_charge_segments
