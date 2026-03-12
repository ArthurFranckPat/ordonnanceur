# Chargeur de données CSV
import pandas as pd
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from datetime import datetime

from .config import Config


class DataLoader:
    """Charge les fichiers CSV d'entrée"""
    
    REQUIRED_FILES = [
        "commandes_clients.csv",
        "stock.csv", 
        "receptions_attendues.csv",
        "of_candidats.csv",
        "composants_of.csv",
        "referentiel_articles.csv"
    ]
    
    OPTIONAL_FILES = [
        "fermetures.csv"
    ]
    
    def __init__(self, data_dir: str, config: Config):
        self.data_dir = Path(data_dir)
        self.config = config
        
    def load_all(self) -> Dict[str, pd.DataFrame]:
        """Charge tous les fichiers CSV"""
        data = {}
        
        # Fichiers requis
        for filename in self.REQUIRED_FILES:
            filepath = self.data_dir / filename
            if not filepath.exists():
                raise FileNotFoundError(f"Fichier requis manquant: {filepath}")
            data[filename] = self._load_csv(filepath)
            
        # Fichiers optionnels
        for filename in self.OPTIONAL_FILES:
            filepath = self.data_dir / filename
            if filepath.exists():
                data[filename] = self._load_csv(filepath)
            else:
                # Créer DataFrame vide avec colonnes attendues
                if filename == "fermetures.csv":
                    data[filename] = pd.DataFrame(columns=["date", "description"])
                    
        return data
    
    def _load_csv(self, filepath: Path) -> pd.DataFrame:
        """Charge un fichier CSV avec parsing des dates"""
        df = pd.read_csv(filepath)
        
        # Parser les colonnes date connues
        date_columns = [
            "shipment_date", "expected_date", "end_date", 
            "date_besoin_prod", "date"
        ]
        
        for col in date_columns:
            if col in df.columns:
                df[col] = pd.to_datetime(df[col], errors="coerce")
                
        return df
    
    def validate_data(self, data: Dict[str, pd.DataFrame]) -> List[str]:
        """Valide les données chargées et retourne les erreurs"""
        errors = []
        
        # Vérifier commandes
        orders = data.get("commandes_clients.csv")
        if orders is not None:
            required_cols = ["order_id", "item_code", "quantity", "shipment_date"]
            missing = [c for c in required_cols if c not in orders.columns]
            if missing:
                errors.append(f"commandes_clients.csv: colonnes manquantes {missing}")
                
            # Vérifier dates valides
            if "shipment_date" in orders.columns:
                invalid_dates = orders["shipment_date"].isna().sum()
                if invalid_dates > 0:
                    errors.append(f"commandes_clients.csv: {invalid_dates} dates d'expédition invalides")
        
        # Vérifier stock
        stock = data.get("stock.csv")
        if stock is not None:
            required_cols = ["item_code", "on_hand", "allocated"]
            missing = [c for c in required_cols if c not in stock.columns]
            if missing:
                errors.append(f"stock.csv: colonnes manquantes {missing}")
                
        # Vérifier composants
        components = data.get("composants_of.csv")
        if components is not None:
            required_cols = ["mo_number", "component_code", "required_qty"]
            missing = [c for c in required_cols if c not in components.columns]
            if missing:
                errors.append(f"composants_of.csv: colonnes manquantes {missing}")
                
        return errors
