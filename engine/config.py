# Configuration du moteur
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

@dataclass
class Config:
    """Paramètres du moteur d'ordonnancement"""
    
    # Horizon temporel
    horizon_weeks: int = 8
    
    # Décalage production (jours ouvrés)
    production_offset_days: int = 2
    
    # Seuil alerte orange
    orange_threshold: float = 0.0  # Ratio < 100% = orange
    
    # Profondeur max nomenclature
    max_bom_depth: int = 5
    
    # Categories non-bloquantes (SF* et PF*)
    non_blocking_categories: tuple = ("SF", "PF")
    
    # Fichier fermetures (optionnel)
    closures_file: Optional[str] = None
    
    # Date de référence (défaut = aujourd'hui)
    reference_date: datetime = None
    
    def __post_init__(self):
        if self.reference_date is None:
            self.reference_date = datetime.now().date()
