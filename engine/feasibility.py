# Verificateur de faisabilite composants
from dataclasses import dataclass, field
from datetime import date
from typing import Dict, List, Optional, Tuple

import pandas as pd

from .atp_calculator import ATPCalculator
from .calendar import BusinessCalendar
from .config import Config


@dataclass
class ComponentStatus:
    """Statut d'un composant pour un OF."""

    component_code: str
    component_desc: str
    category: str
    required_qty: float
    atp_instant: float
    atp_projected: float
    is_covered_instant: bool
    is_covered_projected: bool
    is_blocking: bool
    gap_qty: float
    next_receipt_date: Optional[date]
    next_receipt_qty: float


@dataclass
class FeasibilityResult:
    """Resultat de faisabilite pour un OF alloue a une commande."""

    order_id: str
    line_id: str
    customer: str
    mo_number: str
    item_code: str
    quantity: float
    production_need_date: Optional[date]
    ratio_instant: float
    ratio_projected: float
    status: str
    status_emoji: str
    components: List[ComponentStatus] = field(default_factory=list)
    blocking_components: List[ComponentStatus] = field(default_factory=list)
    missing_components: List[str] = field(default_factory=list)
    missing_count: int = 0
    sf_pf_status: Dict[str, str] = field(default_factory=dict)


class FeasibilityChecker:
    """Verifie la faisabilite des OF en consommant les composants sequentiellement."""

    def __init__(
        self,
        config: Config,
        calendar: BusinessCalendar,
        atp_calculator: ATPCalculator,
    ):
        self.config = config
        self.calendar = calendar
        self.atp_calculator = atp_calculator

    def check_feasibility(
        self,
        allocation_results: pd.DataFrame,
        components_df: pd.DataFrame,
        atp_df: pd.DataFrame,
        items_df: pd.DataFrame,
        receipts_df: pd.DataFrame,
    ) -> Tuple[pd.DataFrame, List[FeasibilityResult]]:
        """Verifie les OF alloues dans l'ordre de priorite et consomme les composants."""
        item_categories = self._build_item_categories(items_df)
        item_descriptions = self._build_item_descriptions(items_df)
        receipts_by_item = self._build_receipts_map(receipts_df)
        component_consumption = self._init_component_consumption(atp_df)

        of_allocations = allocation_results[allocation_results["mo_number"].notna()].copy()
        if of_allocations.empty:
            return pd.DataFrame(), []

        of_allocations = of_allocations.sort_values(
            by=["production_need_date", "order_id", "line_id"],
            ascending=[True, True, True],
            na_position="last",
        )

        results: List[FeasibilityResult] = []
        for _, allocation in of_allocations.iterrows():
            result = self._check_single_allocation(
                allocation,
                components_df,
                atp_df,
                item_categories,
                item_descriptions,
                receipts_by_item,
                component_consumption,
            )
            results.append(result)

        return self._results_to_dataframe(results), results

    def _build_item_categories(self, items_df: pd.DataFrame) -> Dict[str, str]:
        mapping: Dict[str, str] = {}
        if items_df.empty:
            return mapping
        for _, row in items_df.iterrows():
            item_code = row.get("item_code")
            if pd.notna(item_code) and item_code:
                mapping[str(item_code)] = str(row.get("category", "") or "")
        return mapping

    def _build_item_descriptions(self, items_df: pd.DataFrame) -> Dict[str, str]:
        mapping: Dict[str, str] = {}
        if items_df.empty:
            return mapping
        for _, row in items_df.iterrows():
            item_code = row.get("item_code")
            if pd.notna(item_code) and item_code:
                mapping[str(item_code)] = str(row.get("item_desc", "") or "")
        return mapping

    def _build_receipts_map(self, receipts_df: pd.DataFrame) -> Dict[str, List[Tuple[date, float]]]:
        mapping: Dict[str, List[Tuple[date, float]]] = {}
        if receipts_df.empty:
            return mapping

        for _, row in receipts_df.iterrows():
            item_code = row.get("item_code")
            expected_date = row.get("expected_date")
            quantity = float(row.get("quantity", 0.0) or 0.0)

            if pd.isna(item_code) or not item_code or pd.isna(expected_date):
                continue

            parsed_date = expected_date.date() if hasattr(expected_date, "date") else expected_date
            mapping.setdefault(str(item_code), []).append((parsed_date, quantity))

        for item_code, receipts in mapping.items():
            mapping[item_code] = sorted(receipts, key=lambda receipt: receipt[0])

        return mapping

    def _init_component_consumption(self, atp_df: pd.DataFrame) -> Dict[str, Dict[str, float]]:
        tracking: Dict[str, Dict[str, float]] = {}
        for _, row in atp_df.iterrows():
            tracking[str(row["item_code"])] = {
                "instant_consumed": 0.0,
                "projected_consumed": 0.0,
            }
        return tracking

    def _check_single_allocation(
        self,
        allocation: pd.Series,
        components_df: pd.DataFrame,
        atp_df: pd.DataFrame,
        item_categories: Dict[str, str],
        item_descriptions: Dict[str, str],
        receipts_by_item: Dict[str, List[Tuple[date, float]]],
        component_consumption: Dict[str, Dict[str, float]],
    ) -> FeasibilityResult:
        mo_number = str(allocation.get("mo_number", ""))
        item_code = str(allocation.get("item_code", ""))
        quantity = float(allocation.get("quantity_allocated", 0.0) or 0.0)
        production_need_date = allocation.get("production_need_date")

        mo_components = components_df[components_df["mo_number"] == mo_number].copy() if not components_df.empty else pd.DataFrame()

        if mo_components.empty:
            return FeasibilityResult(
                order_id=str(allocation.get("order_id", "")),
                line_id=str(allocation.get("line_id", "")),
                customer=str(allocation.get("customer", "")),
                mo_number=mo_number,
                item_code=item_code,
                quantity=quantity,
                production_need_date=production_need_date,
                ratio_instant=1.0,
                ratio_projected=1.0,
                status="green",
                status_emoji="🟢",
            )

        component_statuses: List[ComponentStatus] = []
        blocking_components: List[ComponentStatus] = []
        sf_pf_status: Dict[str, str] = {}

        for _, component in mo_components.iterrows():
            component_code = str(component.get("component_code", ""))
            component_desc = str(
                component.get("component_desc")
                or item_descriptions.get(component_code, "")
            )
            per_unit_qty = float(component.get("required_qty", 0.0) or 0.0)
            required_qty = per_unit_qty * quantity
            category = item_categories.get(component_code, "")
            is_blocking = not self._is_non_blocking_category(category)

            available_instant, available_projected = self._get_available_component_atp(
                component_code,
                atp_df,
                production_need_date,
                component_consumption,
            )

            is_covered_instant = available_instant >= required_qty
            is_covered_projected = available_projected >= required_qty
            gap_qty = max(0.0, required_qty - available_projected)
            next_receipt_date, next_receipt_qty = self._get_next_receipt(
                component_code,
                receipts_by_item,
                production_need_date,
            )

            status = ComponentStatus(
                component_code=component_code,
                component_desc=component_desc,
                category=category,
                required_qty=required_qty,
                atp_instant=available_instant,
                atp_projected=available_projected,
                is_covered_instant=is_covered_instant,
                is_covered_projected=is_covered_projected,
                is_blocking=is_blocking,
                gap_qty=gap_qty,
                next_receipt_date=next_receipt_date,
                next_receipt_qty=next_receipt_qty,
            )
            component_statuses.append(status)

            if is_blocking:
                self._consume_component(
                    component_code,
                    required_qty,
                    component_consumption,
                )
                if not is_covered_projected:
                    blocking_components.append(status)
            else:
                sf_pf_status[component_code] = "🟢" if is_covered_projected else "🔴"

        ratio_instant = self._calculate_ratio(component_statuses, use_projected=False)
        ratio_projected = self._calculate_ratio(component_statuses, use_projected=True)
        status_code, status_emoji = self._determine_status(ratio_instant, ratio_projected)

        return FeasibilityResult(
            order_id=str(allocation.get("order_id", "")),
            line_id=str(allocation.get("line_id", "")),
            customer=str(allocation.get("customer", "")),
            mo_number=mo_number,
            item_code=item_code,
            quantity=quantity,
            production_need_date=production_need_date,
            ratio_instant=ratio_instant,
            ratio_projected=ratio_projected,
            status=status_code,
            status_emoji=status_emoji,
            components=component_statuses,
            blocking_components=blocking_components,
            missing_components=[component.component_code for component in blocking_components],
            missing_count=len(blocking_components),
            sf_pf_status=sf_pf_status,
        )

    def _is_non_blocking_category(self, category: str) -> bool:
        for prefix in self.config.non_blocking_categories:
            if category.upper().startswith(prefix.upper()):
                return True
        return False

    def _get_available_component_atp(
        self,
        component_code: str,
        atp_df: pd.DataFrame,
        target_date: Optional[date],
        component_consumption: Dict[str, Dict[str, float]],
    ) -> Tuple[float, float]:
        row = atp_df[atp_df["item_code"] == component_code]
        if row.empty:
            return 0.0, 0.0

        base_instant = float(row.iloc[0].get("atp_instant", 0.0) or 0.0)
        base_projected = (
            float(self.atp_calculator.get_atp_at_date(atp_df, component_code, target_date))
            if target_date is not None
            else base_instant
        )

        consumed = component_consumption.setdefault(
            component_code,
            {"instant_consumed": 0.0, "projected_consumed": 0.0},
        )
        available_instant = max(0.0, base_instant - consumed["instant_consumed"])
        available_projected = max(0.0, base_projected - consumed["projected_consumed"])
        return available_instant, available_projected

    def _consume_component(
        self,
        component_code: str,
        required_qty: float,
        component_consumption: Dict[str, Dict[str, float]],
    ) -> None:
        consumed = component_consumption.setdefault(
            component_code,
            {"instant_consumed": 0.0, "projected_consumed": 0.0},
        )
        consumed["instant_consumed"] += required_qty
        consumed["projected_consumed"] += required_qty

    def _get_next_receipt(
        self,
        component_code: str,
        receipts_by_item: Dict[str, List[Tuple[date, float]]],
        after_date: Optional[date],
    ) -> Tuple[Optional[date], float]:
        receipts = receipts_by_item.get(component_code, [])
        if not receipts:
            return None, 0.0

        if after_date is None:
            return receipts[0]

        for receipt_date, quantity in receipts:
            if receipt_date > after_date:
                return receipt_date, quantity
        return None, 0.0

    def _calculate_ratio(self, components: List[ComponentStatus], use_projected: bool) -> float:
        blocking = [component for component in components if component.is_blocking]
        if not blocking:
            return 1.0

        covered = 0
        for component in blocking:
            is_covered = component.is_covered_projected if use_projected else component.is_covered_instant
            if is_covered:
                covered += 1
        return covered / len(blocking)

    def _determine_status(self, ratio_instant: float, ratio_projected: float) -> Tuple[str, str]:
        if ratio_projected >= 1.0:
            if ratio_instant >= 1.0:
                return "green", "🟢"
            return "orange", "🟠"
        return "red", "🔴"

    def _results_to_dataframe(self, results: List[FeasibilityResult]) -> pd.DataFrame:
        rows = []
        for result in results:
            rows.append(
                {
                    "order_id": result.order_id,
                    "line_id": result.line_id,
                    "customer": result.customer,
                    "mo_number": result.mo_number,
                    "item_code": result.item_code,
                    "quantity": result.quantity,
                    "production_need_date": result.production_need_date,
                    "ratio_instant": result.ratio_instant,
                    "ratio_projected": result.ratio_projected,
                    "status": result.status,
                    "status_emoji": result.status_emoji,
                    "missing_components": ", ".join(result.missing_components),
                    "missing_count": result.missing_count,
                    "sf_pf_status": ", ".join(
                        f"{code}:{status}" for code, status in result.sf_pf_status.items()
                    ),
                }
            )
        return pd.DataFrame(rows)

    def get_missing_components_detail(self, results: List[FeasibilityResult]) -> pd.DataFrame:
        rows = []
        for result in results:
            for component in result.blocking_components:
                rows.append(
                    {
                        "order_id": result.order_id,
                        "line_id": result.line_id,
                        "customer": result.customer,
                        "mo_number": result.mo_number,
                        "component_code": component.component_code,
                        "component_desc": component.component_desc,
                        "required_qty": component.required_qty,
                        "atp_instant": component.atp_instant,
                        "atp_projected": component.atp_projected,
                        "gap_qty": component.gap_qty,
                        "next_receipt_date": component.next_receipt_date,
                        "next_receipt_qty": component.next_receipt_qty,
                        "is_covered_instant": component.is_covered_instant,
                        "is_covered_projected": component.is_covered_projected,
                    }
                )
        return pd.DataFrame(rows)
