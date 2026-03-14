from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import List


STATUTS_NON_AFFERMIS: List[int] = [2, 3]
STATUTS_FERMES_LANCES: List[int] = [1]
PREFIXES_NON_BLOQUANTS: List[str] = ["SF", "PF"]


@dataclass
class Config:
    horizon_jours: int = 14
    mode_injection: str = "conservateur"
    deduire_allocations: bool = True
    allocation_sequentielle: bool = True
    perimetre_chargement: str = "tous"
    capacite_heures_semaine: float = 70.0
    date_reference: datetime = field(default_factory=lambda: datetime.now().replace(hour=0, minute=0, second=0, microsecond=0))
    jours_ouvres_avant_expedition: int = 2
    separateur_csv: str = ";"
    encoding: str = "latin-1"
    dossier_data: str = "data/"
    dossier_output: str = "output/"

    def to_params_dict(self) -> dict:
        return {
            "horizon_jours": self.horizon_jours,
            "mode_injection": self.mode_injection,
            "deduire_allocations": self.deduire_allocations,
            "allocation_sequentielle": self.allocation_sequentielle,
            "perimetre_chargement": self.perimetre_chargement,
            "capacite_heures_semaine": self.capacite_heures_semaine,
            "date_reference": self.date_reference,
            "jours_ouvres_avant_expedition": self.jours_ouvres_avant_expedition,
            "separateur_csv": self.separateur_csv,
            "encoding": self.encoding,
            "dossier_data": self.dossier_data,
            "dossier_output": self.dossier_output,
        }

    @classmethod
    def from_params_dict(cls, params: dict) -> "Config":
        return cls(
            horizon_jours=int(params.get("horizon_jours", 14)),
            mode_injection=str(params.get("mode_injection", "conservateur")),
            deduire_allocations=bool(params.get("deduire_allocations", True)),
            allocation_sequentielle=bool(params.get("allocation_sequentielle", True)),
            perimetre_chargement=str(params.get("perimetre_chargement", "tous")),
            capacite_heures_semaine=float(params.get("capacite_heures_semaine", 70.0)),
            date_reference=params.get("date_reference", datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)),
            jours_ouvres_avant_expedition=int(params.get("jours_ouvres_avant_expedition", 2)),
            separateur_csv=str(params.get("separateur_csv", ";")),
            encoding=str(params.get("encoding", "latin-1")),
            dossier_data=str(params.get("dossier_data", "data/")),
            dossier_output=str(params.get("dossier_output", "output/")),
        )
