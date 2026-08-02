# Copilot Instructions for Harbor（中文版）

> 这些指令用于约束 GitHub Copilot 的代码生成行为，基于 Google Python Style Guide 并针对 Harbor 项目进行适配。

---

## 1. 项目背景

Harbor 是一个**港股与美股**低频量化研究与模拟交易系统。核心特征：
- Python 3.11+，使用 Pydantic 配置管理
- 严格分层架构：`core/`（纯逻辑）→ `infrastructure/`（外部依赖）→ `services/`（业务编排）→ `api/` 或 `cli/`（入口）
- 数据管道强调：**幂等性**、**可追溯性**（每条派生数据必须关联 `ingestion_run_id`）、**不可变性**（原始数据永不修改）
- 风控优先：回撤 5%/8%/10% 三级熔断，单笔风险 ≤ 0.5%
- 双市场支持：**港股（HK）** 与 **美股（US）** 代码和数据在存储层使用 `(market, symbol)` 复合主键区分

---

## 2. 代码生成通用约束

### 2.1 必须包含的内容
- ✅ 所有公共函数必须有完整的类型注解（`def foo(x: int) -> str:`）
- ✅ 所有类、公共函数必须有 docstring
- ✅ 所有外部调用（网络/数据库/文件）必须用 `try/except` 包裹
- ✅ 所有数据写入操作必须支持幂等性（使用 `ON CONFLICT` 或先查后插）
- ✅ 所有时间戳必须显式标注时区（`datetime.now(timezone.utc)`）
- ✅ 涉及多市场的代码必须显式处理 `market` 参数（`Literal["HK", "US"]`）

### 2.2 禁止生成的内容
- ❌ 禁止在 `core/` 目录下生成任何依赖外部服务的代码
- ❌ 禁止生成硬编码的敏感信息（密码、Token、API Key）
- ❌ 禁止生成 `except Exception: pass` 这样的空异常处理
- ❌ 禁止生成 SQL 字符串拼接（必须使用 SQLAlchemy 参数化查询）
- ❌ 禁止使用 `assert` 验证函数前置条件（`assert` 不保证执行）
- ❌ 禁止假设 `symbol` 可以唯一标识一只股票，必须结合 `market` 使用

---

## 3. 数据模型注意事项

### 3.1 复合主键
所有与股票相关的表必须使用 `(market, symbol)` 作为复合主键或唯一约束：

```python
# ✅ 正确
class DailyQuote(Base):
    __tablename__ = "daily_quotes"
    market: Mapped[str] = mapped_column(String(2), primary_key=True)
    symbol: Mapped[str] = mapped_column(String(20), primary_key=True)
    date: Mapped[date] = mapped_column(Date, primary_key=True)
    # ... 其他字段

# ❌ 错误：仅使用 symbol
class DailyQuote(Base):
    __tablename__ = "daily_quotes"
    symbol: Mapped[str] = mapped_column(String(20), primary_key=True)
    # 同一代码在不同市场（如 AAPL vs 00001.HK）会冲突
```

### 3.2 市场枚举
使用字面量类型约束 `market` 参数：

```python
from typing import Literal

MarketType = Literal["HK", "US"]

def get_daily_bars(market: MarketType, symbol: str, start: date, end: date) -> pd.DataFrame:
    ...
```

---

## 4. 导入规范

### 4.1 基本原则
- 使用 `import x` 导入包和模块，**不**导入单个类型、类或函数
- 使用 `from x import y`，其中 `x` 是包前缀，`y` 是不带前缀的模块名
- 仅在以下情况使用 `from x import y as z`：两个同名模块冲突、与当前模块顶层名称冲突、名称过长、名称过于通用
- 使用 `import y as z` 仅在 `z` 是标准缩写时（如 `import numpy as np`）

### 4.2 禁止的行为
- ❌ 禁止使用相对路径导入（如 `from . import module`）
- ❌ 禁止假设 `sys.path` 包含当前目录
- ❌ 禁止将导入分散在文件中间，所有导入必须集中在文件顶部

### 4.3 示例

```python
# ✅ 正确：导入模块
import pandas as pd
import structlog
from sqlalchemy import select

# ✅ 正确：从包中导入模块
from harbor.core.interfaces import MarketDataProviderABC

# ❌ 错误：导入单个类
from pandas import DataFrame

# ❌ 错误：相对导入
from ..core import interfaces
```

---

## 5. 异常处理

### 5.1 基本原则
- 合理使用内置异常类型，如 `ValueError` 表示编程错误或前置条件违反
- **禁止使用 `assert` 验证前置条件**，`assert` 可能在优化模式下被移除
- 自定义异常应继承自现有异常类，名称以 `Error` 结尾
- **禁止使用 `except:` 捕获所有异常**
- 最小化 `try/except` 块中的代码量

### 5.2 例外情况
仅在以下情况允许 `except Exception`：
- 重新抛出异常
- 创建隔离点（如保护线程不崩溃）

### 5.3 示例

```python
# ✅ 正确：使用 ValueError 验证参数
def connect_to_port(self, minimum: int) -> int:
    if minimum < 1024:
        raise ValueError(f"Minimum port must be at least 1024, not {minimum}.")
    # ... 业务逻辑

# ✅ 正确：pytest 中使用 assert
def test_calculate_roe():
    assert calculate_roe(100, 10) == 0.1

# ❌ 错误：使用 assert 验证前置条件
def connect_to_port(self, minimum: int) -> int:
    assert minimum >= 1024, "Minimum port must be at least 1024."  # ❌ 可能被跳过
    # ... 业务逻辑
```

---

## 6. 可变全局状态

- **避免可变全局状态**
- 如有必要，可变全局实体应声明在模块级别，名称以 `_` 开头
- 外部访问必须通过公共函数或类方法
- 模块级常量允许且鼓励，使用全大写加下划线命名

### 示例

```python
# ✅ 正确：内部常量
_MAX_RETRY_COUNT = 3

# ✅ 正确：公共 API 常量
DEFAULT_TIMEOUT = 30

# ❌ 错误：可变全局状态（除非有充分理由并添加注释）
current_position = {}  # ❌ 避免
```

---

## 7. 嵌套/局部/内部类与函数

- 嵌套局部函数或类在用于闭包局部变量时是允许的
- **不要**仅为了对模块用户隐藏而嵌套函数，应在模块级使用 `_` 前缀使其可被测试访问

### 示例

```python
# ✅ 正确：嵌套函数用于闭包
def make_multiplier(n: int):
    def multiplier(x: int) -> int:
        return x * n
    return multiplier

# ✅ 正确：模块级私有函数（可测试）
def _helper_function(x: int) -> int:
    return x + 1

# ❌ 错误：仅为隐藏而嵌套
def public_function(x: int) -> int:
    def _hidden_helper(y: int) -> int:  # ❌ 难以测试
        return y + 1
    return _hidden_helper(x) + x
```

---

## 8. 推导式与生成器表达式

- 允许用于简单场景
- **禁止多个 `for` 子句或过滤表达式**——可读性优先
- 复杂逻辑应使用普通循环

### 示例

```python
# ✅ 正确：简单推导式
result = [x**2 for x in range(10) if x % 2 == 0]
unique_names = {user.name for user in users if user is not None}

# ❌ 错误：多个 for 子句
result = [(x, y) for x in range(10) for y in range(5) if x * y > 10]  # ❌ 难以阅读

# ✅ 正确：复杂逻辑使用普通循环
result = []
for x in range(10):
    for y in range(5):
        if x * y > 10:
            result.append((x, y))
```

---

## 9. 默认迭代器与操作符

- 使用类型的默认迭代器和操作符（`for key in adict:` 而非 `for key in adict.keys():`）
- 迭代时**不要**修改容器

### 示例

```python
# ✅ 正确
for key in adict:
    ...

if obj in alist:
    ...

for line in afile:
    ...

for k, v in adict.items():
    ...

# ❌ 错误
for key in adict.keys():
    ...

for line in afile.readlines():
    ...
```

---

## 10. 生成器

- 按需使用
- 在 docstring 中使用 `Yields:` 而非 `Returns:`
- 如生成器管理昂贵资源，使用上下文管理器确保清理

---

## 11. Lambda 函数

- 仅允许单行 Lambda
- Lambda 内代码超过 60-80 字符时，应定义为常规嵌套函数
- 优先使用 `operator` 模块函数而非 Lambda

### 示例

```python
# ✅ 正确：简单 Lambda
sorted_data = sorted(data, key=lambda x: x['date'])

# ✅ 正确：使用 operator 模块
from operator import mul
result = list(map(mul, [1, 2, 3], [4, 5, 6]))

# ❌ 错误：Lambda 过长
# 应改为嵌套函数
```

---

## 12. 条件表达式

- 允许用于简单场景
- 每个部分必须在一行内完成
- 复杂情况使用完整 `if` 语句

### 示例

```python
# ✅ 正确
one_line = 'yes' if predicate(value) else 'no'

# ✅ 正确：多行但每部分在一行内
slightly_split = (
    'yes' if predicate(value) else 'no, nein, nyet'
)

# ❌ 错误：条件过于复杂
# 应使用 if 语句
```

---

## 13. 默认参数值

- 允许使用
- **禁止使用可变对象作为默认值**（如 `def foo(a, b=[]):`）
- 使用 `None` 并在函数内初始化

### 示例

```python
# ✅ 正确
def foo(a, b: list | None = None) -> None:
    if b is None:
        b = []

# ✅ 正确：元组不可变
def foo(a, b: tuple = ()) -> None:
    ...

# ❌ 错误：可变默认值
def foo(a, b=[]) -> None:  # ❌ 所有调用共享同一列表
    ...

# ❌ 错误：默认值在模块加载时求值
def foo(a, b=time.time()) -> None:  # ❌ 不是预期的调用时时间
    ...
```

---

## 14. 属性（Properties）

- 用于控制需要简单计算或逻辑的属性访问
- 属性实现必须符合常规属性访问的预期：廉价、直接、无意外
- **禁止**仅为 get/set 内部属性而使用 property——应直接使用公共属性

### 示例

```python
# ✅ 正确：计算派生值
@property
def total_value(self) -> float:
    return self.quantity * self.price

# ❌ 错误：仅为 get/set 内部属性
@property
def name(self) -> str:
    return self._name

@name.setter
def name(self, value: str) -> None:
    self._name = value
# 应直接使用 public attribute: self.name
```

---

## 15. True/False 求值

- 尽可能使用“隐式”假值（`if foo:` 而非 `if foo != []:`）
- 检查 `None` 时**必须**使用 `if foo is None:` 或 `if foo is not None:`
- **禁止**使用 `==` 比较布尔变量与 `False`，使用 `if not x:`
- 对序列使用 `if seq:` 和 `if not seq:`，而非 `if len(seq):`
- 对 NumPy 数组使用 `.size` 属性

### 示例

```python
# ✅ 正确
if not users:
    print('no users')

if x is None:
    x = []

# ❌ 错误
if len(users) == 0:
    print('no users')

def f(x=None):
    x = x or []  # ❌ 如果 x 是空列表，会被替换为 []
```

---

## 16. 类型注解

- 在源代码中使用类型注解
- 函数和方法参数及返回值必须注解
- 变量也可声明类型：`a: SomeType = some_func()`

### 示例

```python
from typing import Optional, Sequence, Literal

# ✅ 正确：完整的类型注解
def get_daily_bars(
    market: Literal["HK", "US"],
    symbol: str,
    start_date: date,
    end_date: date,
    limit: int = 1000
) -> pd.DataFrame:
    ...

# ✅ 正确：变量类型注解
prices: pd.Series = df['close']
```

---

## 17. 命名规范

| 类型 | 规范 | 示例 |
| :--- | :--- | :--- |
| 模块名 | `lower_with_under.py` | `data_provider.py` |
| 类名 | `CapWords`（PascalCase） | `MarketDataProvider` |
| 异常名 | `CapWords` + `Error` 后缀 | `DataIngestionError` |
| 函数/方法名 | `lower_with_under` | `get_daily_bars()` |
| 常量 | `ALL_CAPS_WITH_UNDER` | `MAX_DRAWDOWN` |
| 变量 | `lower_with_under` | `adjusted_close` |
| 私有属性/方法 | 前缀 `_` | `_validate_symbol()` |
| 内部使用的常量 | 前缀 `_` | `_MAX_RETRY_COUNT` |

---

## 18. Main 函数

- 所有代码应在 `if __name__ == '__main__':` 下执行
- 主函数应调用 `main()` 函数，不直接执行逻辑

### 示例

```python
def main() -> None:
    # 主逻辑

if __name__ == '__main__':
    main()
```

---

## 19. 文件与目录结构约束

| 目录 | 职责 | 允许的导入 | 禁止的行为 |
| :--- | :--- | :--- | :--- |
| `src/harbor/core/` | 纯业务实体与接口定义 | Python 标准库、Pydantic | 导入 SQLAlchemy、requests、基础设施层 |
| `src/harbor/infrastructure/data_providers/` | 数据源实现 | `core/` 中的接口、第三方库 | 在基础设施中定义业务实体 |
| `src/harbor/infrastructure/data_providers/hk/` | 港股数据源（yfinance、akshare） | 父目录接口 | 混入美股逻辑 |
| `src/harbor/infrastructure/data_providers/us/` | 美股数据源（yfinance） | 父目录接口 | 混入港股逻辑 |
| `src/harbor/services/` | 业务流程编排 | `core/` 和 `infrastructure/` | 直接操作数据库 |
| `src/harbor/api/` | FastAPI 路由 | `services/` | 包含业务逻辑 |
| `src/harbor/cli/` | CLI 命令 | `services/` | 包含业务逻辑 |
| `tests/` | 测试代码 | 所有模块 | 测试中连接真实外部服务 |

---

## 20. 过时的数据和企业行动信息

港股和美股市场发生过的历史事件，如分红、拆合股、供股、要约等，必须保留完整记录。数据源（yfinance 等）在用于可信研究前，需验证下列事项：

- 历史成分股与已退市股票应完整覆盖，且可追溯其退市日期与原因
- 退市、停牌、更名后，代码映射需保留历史对照关系
- 分红数据应区分普通股息与特别股息，并标注除净日、登记日与派息日
- 拆合股、供股、要约、分拆等企业行动，应记录其条款、比例和生效日期
- 港股与美股的企业行动规则不同，处理逻辑需分别实现，不得混用
- 已知数据源不完整或存在错误记录时，不得在内部静默修复；必须通过人工复核队列或数据质量报告暴露，并在回测中如实反映其影响

---

## 21. 当前阶段上下文

- **当前 MVP 阶段**：1（数据基础）
- **技术栈**：Python 3.11 + SQLAlchemy 2.0 + Alembic + PostgreSQL + TimescaleDB
- **数据源**：yfinance（港股 + 美股）、AkShare（港股备选）、Mock（测试）
- **配置管理**：Pydantic + `.env` 文件
- **目标市场**：港股（HK）和美股（US），通过 `MARKET_TARGET` 环境变量选择

---

**生效日期**：2026-08-02
**适用版本**：Harbor v0.1.0 (MVP 1)
**参考**：[Google Python Style Guide](https://github.com/google/styleguide/blob/gh-pages/pyguide.md)