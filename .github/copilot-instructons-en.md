# Copilot Instructions for Harbor (English Version)

> These instructions constrain GitHub Copilot's code generation behavior, based on Google Python Style Guide and adapted for the Harbor project.

---

## 1. Project Context

Harbor is a **Hong Kong and U.S. stock** low-frequency quantitative research and simulation trading system. Key characteristics:
- Python 3.11+, uses Pydantic for configuration management
- Strict layered architecture: `core/` (pure logic) → `infrastructure/` (external dependencies) → `services/` (orchestration) → `api/` or `cli/` (entry points)
- Data pipeline emphasizes: **Idempotency**, **Traceability** (every derived data must link to `ingestion_run_id`), **Immutability** (raw data is never modified)
- Risk-first: 5%/8%/10% three-tier drawdown circuit breakers, single trade risk ≤ 0.5%
- Dual-market support: **HK** and **US** stocks are distinguished by `(market, symbol)` composite primary key at storage layer

---

## 2. General Code Generation Constraints

### 2.1 Must Include
- ✅ All public functions must have complete type annotations (`def foo(x: int) -> str:`)
- ✅ All classes and public functions must have docstrings
- ✅ All external calls (network/database/file) must be wrapped with `try/except`
- ✅ All data write operations must support idempotency (use `ON CONFLICT` or check-then-insert)
- ✅ All timestamps must explicitly specify timezone (`datetime.now(timezone.utc)`)
- ✅ Multi-market code must explicitly handle `market` parameter (`Literal["HK", "US"]`)

### 2.2 Must Not Generate
- ❌ Code in `core/` that depends on external services
- ❌ Hardcoded secrets (passwords, tokens, API keys)
- ❌ Empty exception handling like `except Exception: pass`
- ❌ SQL string concatenation (must use SQLAlchemy parameterized queries)
- ❌ Use of `assert` for precondition validation (`assert` may be skipped)
- ❌ Assumption that `symbol` alone uniquely identifies a stock (must combine with `market`)

---

## 3. Data Model Considerations

### 3.1 Composite Primary Key
All stock-related tables must use `(market, symbol)` as composite primary key or unique constraint:

```python
# ✅ Correct
class DailyQuote(Base):
    __tablename__ = "daily_quotes"
    market: Mapped[str] = mapped_column(String(2), primary_key=True)
    symbol: Mapped[str] = mapped_column(String(20), primary_key=True)
    date: Mapped[date] = mapped_column(Date, primary_key=True)
    # ... other fields

# ❌ Wrong: symbol only
class DailyQuote(Base):
    __tablename__ = "daily_quotes"
    symbol: Mapped[str] = mapped_column(String(20), primary_key=True)
    # Same symbol in different markets (e.g., AAPL vs 00001.HK) will conflict
```

### 3.2 Market Enumeration
Use `Literal` to constrain `market` parameter:

```python
from typing import Literal

MarketType = Literal["HK", "US"]

def get_daily_bars(market: MarketType, symbol: str, start: date, end: date) -> pd.DataFrame:
    ...
```

---

## 4. Import Rules

### 4.1 General Rules
- Use `import x` for packages and modules; **do not** import individual types, classes, or functions
- Use `from x import y`, where `x` is the package prefix and `y` is the unprefixed module name
- Only use `from x import y as z` when: two modules conflict, name conflicts with top-level name, name is too long, or name is too generic
- Use `import y as z` only when `z` is a standard abbreviation (e.g., `import numpy as np`)

### 4.2 Must Not
- ❌ Relative imports (e.g., `from . import module`)
- ❌ Assuming `sys.path` includes the current directory
- ❌ Scattered imports — all imports must be at the top of the file

### 4.3 Examples

```python
# ✅ Correct: import modules
import pandas as pd
import structlog
from sqlalchemy import select

# ✅ Correct: import from package
from harbor.core.interfaces import MarketDataProviderABC

# ❌ Wrong: import single class
from pandas import DataFrame

# ❌ Wrong: relative import
from ..core import interfaces
```

---

## 5. Exception Handling

### 5.1 General Rules
- Use built-in exception types appropriately (e.g., `ValueError` for programming errors)
- **Do not use `assert` for precondition validation** — `assert` may be removed in optimized mode
- Custom exceptions should inherit from existing exception classes and end with `Error`
- **Do not use `except:` to catch all exceptions**
- Minimize code inside `try/except` blocks

### 5.2 Exceptions
`except Exception` is allowed only in these cases:
- Re-raising the exception
- Creating an isolation point (e.g., protecting a thread from crashing)

### 5.3 Examples

```python
# ✅ Correct: ValueError for parameter validation
def connect_to_port(self, minimum: int) -> int:
    if minimum < 1024:
        raise ValueError(f"Minimum port must be at least 1024, not {minimum}.")
    # ... business logic

# ✅ Correct: assert in pytest
def test_calculate_roe():
    assert calculate_roe(100, 10) == 0.1

# ❌ Wrong: assert for precondition
def connect_to_port(self, minimum: int) -> int:
    assert minimum >= 1024, "Minimum port must be at least 1024."  # ❌ May be skipped
    # ... business logic
```

---

## 6. Mutable Global State

- **Avoid mutable global state**
- If necessary, mutable global entities should be declared at module level with `_` prefix
- External access must be through public functions or class methods
- Module-level constants are allowed and encouraged; use `UPPER_SNAKE_CASE`

### Examples

```python
# ✅ Correct: internal constant
_MAX_RETRY_COUNT = 3

# ✅ Correct: public API constant
DEFAULT_TIMEOUT = 30

# ❌ Wrong: mutable global state (unless justified with comment)
current_position = {}  # ❌ Avoid
```

---

## 7. Nested/Local/Inner Classes and Functions

- Nested local functions/classes are allowed when used for closure over local variables
- **Do not** nest functions just to hide them from module users; use `_` prefix at module level for testability

### Examples

```python
# ✅ Correct: nested function for closure
def make_multiplier(n: int):
    def multiplier(x: int) -> int:
        return x * n
    return multiplier

# ✅ Correct: module-level private function (testable)
def _helper_function(x: int) -> int:
    return x + 1

# ❌ Wrong: nested just for hiding
def public_function(x: int) -> int:
    def _hidden_helper(y: int) -> int:  # ❌ Hard to test
        return y + 1
    return _hidden_helper(x) + x
```

---

## 8. Comprehensions and Generator Expressions

- Allowed for simple scenarios
- **No multiple `for` clauses or filter expressions** — readability first
- Use regular loops for complex logic

### Examples

```python
# ✅ Correct: simple comprehension
result = [x**2 for x in range(10) if x % 2 == 0]
unique_names = {user.name for user in users if user is not None}

# ❌ Wrong: multiple for clauses
result = [(x, y) for x in range(10) for y in range(5) if x * y > 10]  # ❌ Hard to read

# ✅ Correct: complex logic uses regular loops
result = []
for x in range(10):
    for y in range(5):
        if x * y > 10:
            result.append((x, y))
```

---

## 9. Default Iterators and Operators

- Use default iterators and operators (`for key in adict:` instead of `for key in adict.keys():`)
- **Do not** modify containers while iterating

### Examples

```python
# ✅ Correct
for key in adict:
    ...

if obj in alist:
    ...

for line in afile:
    ...

for k, v in adict.items():
    ...

# ❌ Wrong
for key in adict.keys():
    ...

for line in afile.readlines():
    ...
```

---

## 10. Generators

- Use as needed
- Use `Yields:` instead of `Returns:` in docstring
- If generator manages expensive resources, use context manager to ensure cleanup

---

## 11. Lambda Functions

- Single-line lambdas only
- If lambda exceeds 60-80 characters, define as regular nested function
- Prefer `operator` module functions over lambda

### Examples

```python
# ✅ Correct: simple lambda
sorted_data = sorted(data, key=lambda x: x['date'])

# ✅ Correct: use operator module
from operator import mul
result = list(map(mul, [1, 2, 3], [4, 5, 6]))

# ❌ Wrong: lambda too long
# Should use nested function
```

---

## 12. Conditional Expressions

- Allowed for simple scenarios
- Each part must fit on one line
- Use full `if` statement for complex cases

### Examples

```python
# ✅ Correct
one_line = 'yes' if predicate(value) else 'no'

# ✅ Correct: multi-line but each part on one line
slightly_split = (
    'yes' if predicate(value) else 'no, nein, nyet'
)

# ❌ Wrong: condition too complex
# Should use if statement
```

---

## 13. Default Parameter Values

- Allowed
- **Do not use mutable objects as default values** (e.g., `def foo(a, b=[]):`)
- Use `None` and initialize inside function

### Examples

```python
# ✅ Correct
def foo(a, b: list | None = None) -> None:
    if b is None:
        b = []

# ✅ Correct: tuple is immutable
def foo(a, b: tuple = ()) -> None:
    ...

# ❌ Wrong: mutable default
def foo(a, b=[]) -> None:  # ❌ Shared across all calls
    ...

# ❌ Wrong: default evaluated at module load time
def foo(a, b=time.time()) -> None:  # ❌ Not expected call time
    ...
```

---

## 14. Properties

- Used for attribute access that requires simple computation or logic
- Property implementation must meet expectations of regular attribute access: cheap, straightforward, no surprises
- **Do not** use property only for get/set of internal attributes — use public attribute directly

### Examples

```python
# ✅ Correct: computed derived value
@property
def total_value(self) -> float:
    return self.quantity * self.price

# ❌ Wrong: only for get/set internal attribute
@property
def name(self) -> str:
    return self._name

@name.setter
def name(self, value: str) -> None:
    self._name = value
# Should use public attribute directly: self.name
```

---

## 15. True/False Evaluation

- Use "implicit" false values when possible (`if foo:` instead of `if foo != []:`)
- **Must** use `if foo is None:` or `if foo is not None:` to check `None`
- **Do not** compare boolean variables to `False` using `==`; use `if not x:`
- Use `if seq:` and `if not seq:` for sequences, not `if len(seq):`
- For NumPy arrays, use `.size` attribute

### Examples

```python
# ✅ Correct
if not users:
    print('no users')

if x is None:
    x = []

# ❌ Wrong
if len(users) == 0:
    print('no users')

def f(x=None):
    x = x or []  # ❌ If x is empty list, replaced with []
```

---

## 16. Type Annotations

- Use type annotations in source code
- Function and method parameters and return values must be annotated
- Variables may also be annotated: `a: SomeType = some_func()`

### Examples

```python
from typing import Optional, Sequence, Literal

# ✅ Correct: complete type annotations
def get_daily_bars(
    market: Literal["HK", "US"],
    symbol: str,
    start_date: date,
    end_date: date,
    limit: int = 1000
) -> pd.DataFrame:
    ...

# ✅ Correct: variable annotation
prices: pd.Series = df['close']
```

---

## 17. Naming Conventions

| Type | Convention | Example |
| :--- | :--- | :--- |
| Module name | `lower_with_under.py` | `data_provider.py` |
| Class name | `CapWords` (PascalCase) | `MarketDataProvider` |
| Exception name | `CapWords` + `Error` suffix | `DataIngestionError` |
| Function/Method name | `lower_with_under` | `get_daily_bars()` |
| Constant | `ALL_CAPS_WITH_UNDER` | `MAX_DRAWDOWN` |
| Variable | `lower_with_under` | `adjusted_close` |
| Private attribute/method | `_` prefix | `_validate_symbol()` |
| Internal constant | `_` prefix | `_MAX_RETRY_COUNT` |

---

## 18. Main Function

- All code should execute under `if __name__ == '__main__':`
- Main function should call `main()`, not execute logic directly

### Example

```python
def main() -> None:
    # main logic

if __name__ == '__main__':
    main()
```

---

## 19. File and Directory Structure Constraints

| Directory | Responsibility | Allowed Imports | Prohibited Behavior |
| :--- | :--- | :--- | :--- |
| `src/harbor/core/` | Pure business entities and interfaces | Python stdlib, Pydantic | Import SQLAlchemy, requests, infrastructure |
| `src/harbor/infrastructure/data_providers/` | Data source implementations | Interfaces from `core/`, third-party libs | Define business entities in infrastructure |
| `src/harbor/infrastructure/data_providers/hk/` | HK data sources (yfinance, akshare) | Parent directory interfaces | Mix US logic |
| `src/harbor/infrastructure/data_providers/us/` | US data sources (yfinance) | Parent directory interfaces | Mix HK logic |
| `src/harbor/services/` | Business orchestration | `core/` and `infrastructure/` | Direct database operations |
| `src/harbor/api/` | FastAPI routes | `services/` | Contain business logic |
| `src/harbor/cli/` | CLI commands | `services/` | Contain business logic |
| `tests/` | Test code | All modules | Connect to real external services |

---

## 20. Historical Data and Corporate Actions

Historical events in both HK and US markets (dividends, stock splits/consolidations, rights issues, tender offers, etc.) must be fully recorded. Before using data sources (yfinance, etc.) for reliable research, the following must be verified:

- Historical constituents and delisted stocks must be fully covered, with delisting dates and reasons traceable
- Code mappings must preserve historical relationships after delisting, suspension, or name changes
- Dividend data must distinguish regular vs. special dividends, with ex-date, record date, and payment date clearly marked
- Corporate actions (splits, consolidations, rights issues, tender offers, spin-offs, etc.) must record terms, ratios, and effective dates
- Corporate action rules differ between HK and US markets; processing logic must be implemented separately, not mixed
- Known incomplete or erroneous records from data sources must not be silently fixed internally; they must be exposed through manual review queues or data quality reports, with their impact honestly reflected in backtests

---

## 21. Current Phase Context

- **Current MVP Phase**: 1 (Data Foundation)
- **Tech Stack**: Python 3.11 + SQLAlchemy 2.0 + Alembic + PostgreSQL + TimescaleDB
- **Data Sources**: yfinance (HK + US), AkShare (HK secondary), Mock (testing)
- **Configuration**: Pydantic + `.env` files
- **Target Markets**: HK and US, selected via `MARKET_TARGET` environment variable

---

**Effective Date**: 2026-08-02
**Version**: Harbor v0.1.0 (MVP 1)
**Reference**: [Google Python Style Guide](https://github.com/google/styleguide/blob/gh-pages/pyguide.md)