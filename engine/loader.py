from __future__ import annotations

import pandas as pd
from pathlib import Path
from typing import Dict

from .config import Config, STATUTS_FERMES_LANCES


def _to_num(series: pd.Series) -> pd.Series:
    if series.dtype == object:
        series = series.astype(str).str.strip().str.replace(" ", "").str.replace(",", ".")
    return pd.to_numeric(series, errors="coerce").fillna(0)


def _lire_csv_normalise(
    path: str,
    sep: str,
    enc: str,
    colonnes_attendues: list,
    skipinitialspace: bool = False,
) -> pd.DataFrame:
    df = pd.read_csv(path, sep=sep, encoding=enc, skipinitialspace=skipinitialspace)
    df = df.loc[:, ~df.columns.str.startswith("Unnamed")]
    if len(df.columns) != len(colonnes_attendues):
        raise ValueError(
            f"Schema inattendu pour {path}: {len(df.columns)} colonnes detectees "
            f"({list(df.columns)}) au lieu de {len(colonnes_attendues)} {colonnes_attendues}"
        )
    df.columns = colonnes_attendues
    return df


def charger_donnees(params: dict) -> Dict[str, pd.DataFrame]:
    sep = params["separateur_csv"]
    enc = params["encoding"]
    d = params["dossier_data"]
    dfs: Dict[str, pd.DataFrame] = {}

    art = _lire_csv_normalise(
        d + "articles.csv", sep, enc,
        ["itmref", "designation", "categorie", "type_appro", "delai"],
        skipinitialspace=True,
    )
    art["delai"] = _to_num(art["delai"])
    art["itmref"] = art["itmref"].astype(str).str.strip()
    art["categorie"] = art["categorie"].astype(str).str.strip()
    art["type_appro"] = art["type_appro"].astype(str).str.strip()
    dfs["articles"] = art

    stk = _lire_csv_normalise(
        d + "stock.csv", sep, enc,
        ["itmref", "stock_physique", "stock_alloue", "stock_bloque"],
        skipinitialspace=True,
    )
    stk["itmref"] = stk["itmref"].astype(str).str.strip()
    for c in ["stock_physique", "stock_alloue", "stock_bloque"]:
        stk[c] = _to_num(stk[c])
    dfs["stock"] = stk

    cmd = _lire_csv_normalise(
        d + "commandes_clients.csv", sep, enc,
        ["sohnum", "soplin", "client_code", "client_nom", "itmref", "designation",
         "qte_commandee", "qte_allouee", "qte_restante", "shidat", "flag_contremarque", "mfgnum_lie"],
    )
    cmd["itmref"] = cmd["itmref"].astype(str).str.strip()
    for c in ["qte_commandee", "qte_restante", "qte_allouee"]:
        cmd[c] = _to_num(cmd[c])
    cmd["shidat"] = pd.to_datetime(cmd["shidat"], format="%d/%m/%Y", errors="coerce")
    cmd["flag_contremarque"] = _to_num(cmd["flag_contremarque"]).astype(int)
    cmd["mfgnum_lie"] = cmd["mfgnum_lie"].astype(str).str.strip()
    dfs["commandes"] = cmd

    of = _lire_csv_normalise(
        d + "of_entetes.csv", sep, enc,
        ["mfgnum", "itmref", "designation", "mfgsta", "mfgsta_lib",
         "enddat", "extqty", "cplqty", "qte_restante"],
    )
    of["itmref"] = of["itmref"].astype(str).str.strip()
    of["mfgnum"] = of["mfgnum"].astype(str).str.strip()
    for c in ["extqty", "cplqty", "qte_restante"]:
        of[c] = _to_num(of[c])
    of["enddat"] = pd.to_datetime(of["enddat"], format="%d/%m/%Y", errors="coerce")
    dfs["of_entetes"] = of

    comp = _lire_csv_normalise(
        d + "of_composants.csv", sep, enc,
        ["mfgnum", "composant", "designation", "qty_requise", "dat_besoin"],
    )
    comp["mfgnum"] = comp["mfgnum"].astype(str).str.strip()
    comp["composant"] = comp["composant"].astype(str).str.strip()
    comp["qty_requise"] = _to_num(comp["qty_requise"])
    comp["dat_besoin"] = pd.to_datetime(comp["dat_besoin"], format="%d/%m/%Y", errors="coerce")
    dfs["of_composants"] = comp

    oa = _lire_csv_normalise(
        d + "receptions_oa.csv", sep, enc,
        ["pohnum", "itmref", "fournisseur", "qte_restante", "date_reception"],
    )
    oa["itmref"] = oa["itmref"].astype(str).str.strip()
    oa["qte_restante"] = _to_num(oa["qte_restante"])
    oa["date_reception"] = pd.to_datetime(oa["date_reception"], format="%d/%m/%Y", errors="coerce")
    dfs["receptions_oa"] = oa

    of_fermes = of[(of["mfgsta"].isin(STATUTS_FERMES_LANCES)) & (of["qte_restante"] > 0)].copy()
    dfs["receptions_of"] = of_fermes[["mfgnum", "itmref", "qte_restante", "enddat"]].copy()

    gam = _lire_csv_normalise(
        d + "gammes.csv", sep, enc,
        ["itmref", "poste_charge", "libelle_poste", "cadence"],
        skipinitialspace=True,
    )
    gam["itmref"] = gam["itmref"].astype(str).str.strip()
    gam["poste_charge"] = gam["poste_charge"].astype(str).str.strip()
    gam["cadence"] = gam["cadence"].astype(str).str.replace(",", ".").str.strip()
    gam["cadence"] = pd.to_numeric(gam["cadence"], errors="coerce").fillna(0)
    dfs["gammes"] = gam

    return dfs
