"""Corporate action type mapping and validation for Harbor.

Data providers describe corporate actions with their own vocabularies:
AkShare surfaces Chinese labels such as ``供股`` (rights issue) and ``合股``
(consolidation), while yfinance uses English labels such as ``split``. This
module normalizes those raw labels to the canonical
:class:`~harbor.core.market_registry.CorporateActionType` vocabulary and
enforces that the resolved type is allowed for the target market, so that
Hong Kong and United States action rules are never mixed.
"""

from harbor.config import MarketTarget
from harbor.core.market_registry import CorporateActionType, get_market_config

_ALIASES: dict[str, CorporateActionType] = {
    # Canonical English labels (resolved to themselves).
    "split": CorporateActionType.SPLIT,
    "consolidation": CorporateActionType.CONSOLIDATION,
    "rights_issue": CorporateActionType.RIGHTS_ISSUE,
    "merger": CorporateActionType.MERGER,
    "spin_off": CorporateActionType.SPIN_OFF,
    "tender_offer": CorporateActionType.TENDER_OFFER,
    "dividend": CorporateActionType.DIVIDEND,
    # English aliases.
    "stock split": CorporateActionType.SPLIT,
    "splits": CorporateActionType.SPLIT,
    "reverse split": CorporateActionType.CONSOLIDATION,
    "rights issue": CorporateActionType.RIGHTS_ISSUE,
    "rights": CorporateActionType.RIGHTS_ISSUE,
    "merge": CorporateActionType.MERGER,
    "spin-off": CorporateActionType.SPIN_OFF,
    "spinoff": CorporateActionType.SPIN_OFF,
    "spin off": CorporateActionType.SPIN_OFF,
    "cash dividend": CorporateActionType.DIVIDEND,
    # Chinese labels (Hong Kong and regional exchanges).
    "供股": CorporateActionType.RIGHTS_ISSUE,
    "配股": CorporateActionType.RIGHTS_ISSUE,
    "合股": CorporateActionType.CONSOLIDATION,
    "股份合并": CorporateActionType.CONSOLIDATION,
    "要约": CorporateActionType.TENDER_OFFER,
    "收购要约": CorporateActionType.TENDER_OFFER,
    "股息": CorporateActionType.DIVIDEND,
    "拆股": CorporateActionType.SPLIT,
    "拆细": CorporateActionType.SPLIT,
    "股票拆分": CorporateActionType.SPLIT,
    "并购": CorporateActionType.MERGER,
    "收购": CorporateActionType.MERGER,
    "分拆": CorporateActionType.SPIN_OFF,
}


def _normalize_label(raw: str) -> str:
    """Return a normalized lookup key for a raw label."""
    return raw.strip().lower()


def resolve_action_type(raw: str) -> CorporateActionType:
    """Map a raw vendor label to its canonical action type.

    Args:
        raw: A provider or vendor corporate action label.

    Returns:
        The canonical :class:`CorporateActionType`.

    Raises:
        ValueError: If the label is not recognized.
    """
    label = _normalize_label(raw)
    try:
        return _ALIASES[label]
    except KeyError as error:
        raise ValueError(f"Unknown corporate action type: {raw!r}") from error


def validate_action_type(
    market: MarketTarget, action_type: CorporateActionType
) -> CorporateActionType:
    """Return the action type when it is valid for the market.

    Args:
        market: The market the corporate action belongs to.
        action_type: The canonical action type to validate.

    Returns:
        The validated action type.

    Raises:
        ValueError: If the action type is not allowed for the market.
    """
    allowed = get_market_config(market).corporate_action_types
    if action_type not in allowed:
        raise ValueError(
            f"Corporate action type {action_type.value!r} is not supported for {market.value}."
        )
    return action_type


def canonical_action_type(market: MarketTarget, raw: str) -> CorporateActionType:
    """Resolve a raw label and validate it for a market.

    Combines :func:`resolve_action_type` and :func:`validate_action_type` so a
    single call maps a vendor label to a market-valid action type.
    """
    return validate_action_type(market, resolve_action_type(raw))


def allowed_action_types(market: MarketTarget) -> frozenset[CorporateActionType]:
    """Return the action types a market supports."""
    return get_market_config(market).corporate_action_types
