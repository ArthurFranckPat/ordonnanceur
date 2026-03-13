# Calculateur ATP (Available-to-Promise)
import pandas as pd
from datetime import date
from typing import Dict, Tuple

from .calendar import BusinessCalendar


class ATPCalculator:
    """Calcule l'ATP (Available-to-Promise) par article"""
    
    def __init__(self, calendar: BusinessCalendar):
        self.calendar = calendar
        
    def calculate_atp(
        self,
        stock_df: pd.DataFrame,
        receipts_df: pd.DataFrame,
        reference_date: date
    ) -> pd.DataFrame:
        """
        Calcule l'ATP par article
        
        Returns:
            DataFrame avec colonnes: item_code, atp_instant, atp_by_date (dict)
        """
        # Stock disponible instantané
        stock_atp = stock_df.copy()
        stock_atp["atp_instant"] = stock_atp["on_hand"] - stock_atp.get("allocated", 0)
        
        # Grouper les réceptions par article et date
        if not receipts_df.empty:
            receipts_by_item = receipts_df.groupby(["item_code", "expected_date"])["quantity"].sum().reset_index()
        else:
            receipts_by_item = pd.DataFrame(columns=["item_code", "expected_date", "quantity"])
        
        # Calculer ATP par article
        results = []
        
        for _, row in stock_atp.iterrows():
            item_code = row["item_code"]
            atp_instant = row["atp_instant"]
            
            # Réceptions pour cet article
            item_receipts = receipts_by_item[receipts_by_item["item_code"] == item_code]
            
            # Calculer ATP cumulé par date
            atp_by_date = {}
            cumulative = atp_instant
            
            # Trier les réceptions par date
            item_receipts = item_receipts.sort_values("expected_date")
            
            for _, receipt in item_receipts.iterrows():
                receipt_date = receipt["expected_date"].date() if hasattr(receipt["expected_date"], "date") else receipt["expected_date"]
                cumulative += receipt["quantity"]
                atp_by_date[receipt_date] = cumulative
            
            results.append({
                "item_code": item_code,
                "atp_instant": atp_instant,
                "atp_by_date": atp_by_date,
                "total_projected": cumulative
            })
        
        return pd.DataFrame(results)
    
    def get_atp_at_date(
        self,
        atp_df: pd.DataFrame,
        item_code: str,
        target_date: date
    ) -> float:
        """
        Récupère l'ATP d'un article à une date donnée
        
        Args:
            atp_df: DataFrame retourné par calculate_atp
            item_code: Code article
            target_date: Date cible
            
        Returns:
            ATP disponible à cette date
        """
        row = atp_df[atp_df["item_code"] == item_code]
        
        if row.empty:
            return 0.0
            
        atp_by_date = row.iloc[0]["atp_by_date"]
        atp_instant = row.iloc[0]["atp_instant"]
        
        # Trouver l'ATP à la date la plus proche <= target_date
        applicable_dates = [d for d in atp_by_date.keys() if d <= target_date]
        
        if not applicable_dates:
            return atp_instant
            
        latest_date = max(applicable_dates)
        return atp_by_date[latest_date]
