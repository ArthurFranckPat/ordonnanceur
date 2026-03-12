# Gestion du calendrier des jours ouvrés
import numpy as np
import pandas as pd
from datetime import datetime, timedelta, date
from typing import List, Optional, Set

try:
    import holidays
    HOLIDAYS_AVAILABLE = True
except ImportError:
    HOLIDAYS_AVAILABLE = False


class BusinessCalendar:
    """Gère le calcul des jours ouvrés"""
    
    def __init__(
        self,
        closures: Optional[pd.DataFrame] = None,
        country: str = "FR",
        weekmask: str = "1111100"  # Lun-Ven = ouvrés
    ):
        """
        Args:
            closures: DataFrame avec colonnes [date, description]
            country: Code pays pour jours fériés (FR, US, etc.)
            weekmask: 7 chars, 1=ouvré, 0=weekend (Lun-Dim)
        """
        self.weekmask = weekmask
        self.closures = self._parse_closures(closures)
        self.country = country
        self.holidays = self._load_holidays(country)
        
    def _parse_closures(self, closures: Optional[pd.DataFrame]) -> Set[date]:
        """Parse les fermetures personnalisées"""
        if closures is None or closures.empty:
            return set()
            
        closure_dates = set()
        for _, row in closures.iterrows():
            try:
                d = pd.to_datetime(row["date"]).date()
                closure_dates.add(d)
            except:
                pass
                
        return closure_dates
    
    def _load_holidays(self, country: str) -> List[date]:
        """Charge les jours fériés pour le pays"""
        if not HOLIDAYS_AVAILABLE:
            # Fallback: jours fériés France codés en dur
            return self._get_french_holidays()
        
        try:
            year = datetime.now().year
            holiday_obj = holidays.CountryHoliday(country, years=range(year, year + 2))
            return list(holiday_obj.keys())
        except:
            return self._get_french_holidays()
    
    def _get_french_holidays(self) -> List[date]:
        """Jours fériés France (fallback)"""
        year = datetime.now().year
        return [
            date(year, 1, 1),    # Jour de l'an
            date(year, 5, 1),    # Fête du travail
            date(year, 5, 8),    # Victoire 1945
            date(year, 7, 14),   # Fête nationale
            date(year, 8, 15),   # Assomption
            date(year, 11, 1),   # Toussaint
            date(year, 11, 11),  # Armistice
            date(year, 12, 25),  # Noël
        ]
    
    def is_business_day(self, d: date) -> bool:
        """Vérifie si une date est un jour ouvré"""
        # Vérifier weekend via weekmask
        weekday = d.weekday()  # 0=Lundi, 6=Dimanche
        if self.weekmask[weekday] == "0":
            return False
            
        # Vérifier jour férié
        if d in self.holidays:
            return False
            
        # Vérifier fermeture personnalisée
        if d in self.closures:
            return False
            
        return True
    
    def add_business_days(self, start_date: date, n_days: int) -> date:
        """Ajoute n jours ouvrés à une date"""
        if n_days == 0:
            return start_date
            
        # Utiliser numpy pour efficacité
        start_np = np.datetime64(start_date)
        
        # Construire liste des jours fériés + fermetures au format numpy
        all_exclusions = sorted(list(self.holidays) + list(self.closures))
        holidays_np = [np.datetime64(d) for d in all_exclusions]
        
        result = np.busday_offset(
            start_np,
            n_days,
            roll="forward",
            holidays=holidays_np if holidays_np else None,
            weekmask=self.weekmask
        )
        
        return result.astype(date)
    
    def subtract_business_days(self, start_date: date, n_days: int) -> date:
        """Soustrait n jours ouvrés d'une date"""
        return self.add_business_days(start_date, -n_days)
    
    def get_business_days_between(self, start_date: date, end_date: date) -> int:
        """Nombre de jours ouvrés entre deux dates"""
        if start_date > end_date:
            return 0
            
        start_np = np.datetime64(start_date)
        end_np = np.datetime64(end_date)
        
        all_exclusions = sorted(list(self.holidays) + list(self.closures))
        holidays_np = [np.datetime64(d) for d in all_exclusions]
        
        return np.busday_count(
            start_np,
            end_np,
            holidays=holidays_np if holidays_np else None,
            weekmask=self.weekmask
        )
