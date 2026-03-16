from __future__ import annotations

from datetime import datetime
from typing import Tuple

import pandas as pd

from .allocator import allouer_stock_commandes, apparier_commandes_of, preparer_donnees
from .assembler import assembler
from .config import Config
from .exporter import export_xl
from .feasibility import alloc_seq_composants, eval_of
from .loader import charger_donnees
from .satisfaction import aggreg_par_client, calculer_satisfaction_s1, filtrer_commandes_s1, resume_global
from .scheduler import calculer_plan_charge
from .supply_chain import construire_chaine_commande, generer_graphviz

__version__ = "3.0.0"

PARAMS = {
    "horizon_jours": 14,
    "mode_injection": "conservateur",
    "deduire_allocations": True,
    "allocation_sequentielle": True,
    "perimetre_chargement": "tous",
    "capacite_heures_semaine": 70,
    "date_reference": datetime.now().replace(hour=0, minute=0, second=0, microsecond=0),
    "jours_ouvres_avant_expedition": 2,
    "separateur_csv": ";",
    "encoding": "latin-1",
    "dossier_data": "data/",
    "dossier_output": "output/",
}


def run(
    params: dict | None = None,
) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    if params is None:
        params = PARAMS
    alloc_str = "OUI" if params["allocation_sequentielle"] else "NON"
    print("=" * 70)
    print("  ORDONNANCEMENT OF v3 PROD — Aereco")
    print(
        f"  Date ref: {params['date_reference'].strftime('%d/%m/%Y')} "
        f"| Horizon {params['horizon_jours']}j | Alloc seq: {alloc_str}"
    )
    print("=" * 70)

    print("\n[1/9] Chargement...")
    dfs = charger_donnees(params)
    print(
        f"  {len(dfs['commandes'])} cmd | {len(dfs['of_entetes'])} OF "
        f"| {len(dfs['of_composants'])} comp"
    )
    print(
        f"  {len(dfs['stock'])} stk | {len(dfs['receptions_oa'])} OA "
        f"| {len(dfs['receptions_of'])} rec OF | {len(dfs['gammes'])} gammes"
    )

    print("\n[2/9] Preparation...")
    dfs = preparer_donnees(dfs, params)
    cmd = dfs["commandes"]
    print(
        f"  {len(cmd)} lignes horizon "
        f"| {len(cmd[cmd['type_flux'] == 'contremarque'])} ctm "
        f"| {len(cmd[cmd['type_flux'] == 'sur_stock'])} stock"
    )
    print(
        f"  {len(dfs['of_non_affermis'])} OF non affermis "
        f"| {len(dfs['of_fermes_lances'])} OF fermes"
    )

    print("\n[3/9] Allocation stock PF...")
    cmd = allouer_stock_commandes(dfs)
    print(f"  {len(cmd[cmd['source_couverture'] == 'stock'])} couvertes par stock")

    print("\n[4/9] Appariement cmd->OF...")
    cmd = apparier_commandes_of(dfs)
    print(f"  {len(cmd[cmd['source_couverture'].str.contains('aucun', na=False)])} sans OF")

    print("\n[5/9] Identification OF...")
    of_list = list(
        cmd[cmd["of_associe"].astype(str).str.strip().ne("")]["of_associe"].unique()
    )
    of_na_set = set(dfs["of_non_affermis"]["mfgnum"].values)
    of_eval = [o for o in of_list if o in of_na_set]
    of_ferme = [o for o in of_list if o not in of_na_set]
    print(f"  {len(of_eval)} OF non affermis | {len(of_ferme)} OF fermes (seront aussi evalues)")

    print("\n[6/9] Faisabilite + multi-niveaux recursif...")
    if of_eval:
        res_of = alloc_seq_composants(of_eval, dfs, params)
        nb_enf = sum(len(r.get("of_enfants", [])) for r in res_of.values())
        print(f"  {len(res_of)} OF parents | {nb_enf} OF enfants decouverts")
    else:
        res_of = {}
        print("  Aucun OF non affermi")

    print("\n[7/9] Chargement capacitaire (backscheduling)...")
    df_plan, of_capa, df_charge_segments = calculer_plan_charge(dfs, res_of, params)
    nb_gammes = len([m for m, i in of_capa.items() if not i.get("alerte")])
    nb_sans_gamme = len([m for m, i in of_capa.items() if i.get("alerte") == "Gamme manquante"])
    print(f"  {nb_gammes} OF avec gamme | {nb_sans_gamme} sans gamme")
    if not df_plan.empty:
        pct_cols = [c for c in df_plan.columns if c.endswith("(%)")]
        surcharges = sum(int((df_plan[c] > 100).sum()) for c in pct_cols)
        print(f"  {len(pct_cols)} postes charges | {surcharges} semaines en surcharge (>100%)")

    print("\n[8/9] Assemblage (+ evaluation OF fermes)...")
    df_cmd, df_det, df_crit = assembler(dfs, res_of, of_capa)

    if not df_charge_segments.empty:
        cmd_for_order = dfs["commandes"].copy()
        cmd_for_order = cmd_for_order[
            cmd_for_order["of_associe"].astype(str).str.strip().ne("")
        ].copy()
        if not cmd_for_order.empty:
            of_order_info = (
                cmd_for_order.groupby("of_associe", as_index=False)
                .agg({"date_besoin_prod": "min", "shidat": "min"})
                .rename(columns={
                    "of_associe": "MFGNUM",
                    "date_besoin_prod": "Date besoin cmd",
                    "shidat": "Date expedition cmd",
                })
            )
            df_charge_segments = df_charge_segments.merge(of_order_info, on="MFGNUM", how="left")
        else:
            df_charge_segments["Date besoin cmd"] = pd.NaT
            df_charge_segments["Date expedition cmd"] = pd.NaT

    print("\n[9/9] Resultats")
    print("=" * 70)
    if not df_cmd.empty:
        nv = len(df_cmd[df_cmd["Feu Ligne"] == "VERT"])
        no = len(df_cmd[df_cmd["Feu Ligne"] == "ORANGE"])
        nr = len(df_cmd[df_cmd["Feu Ligne"] == "ROUGE"])
        nse = len(df_cmd[df_cmd["Type Flux"] == "sous-ensemble"])
        print(f"  VERT {nv} | ORANGE {no} | ROUGE {nr} | Sous-ensembles {nse}")
    if not df_crit.empty:
        print("\n  Top composants critiques:")
        for _, r in df_crit.head(10).iterrows():
            print(
                f"    {r['Composant']:15s} couv {r['Taux Couverture (%)']:6.1f}% "
                f"— {r['Nb OF']} OF, {r['Nb Commandes']} cmd"
            )
    return df_cmd, df_det, df_plan, df_crit, df_charge_segments


__all__ = [
    "run", "export_xl", "PARAMS", "Config",
    "calculer_satisfaction_s1", "filtrer_commandes_s1", "aggreg_par_client", "resume_global",
    "construire_chaine_commande", "generer_graphviz",
]
