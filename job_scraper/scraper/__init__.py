from collections.abc import Callable, Coroutine
from typing import Any

type GetFn = Callable[[str], Coroutine[Any, Any, str]]
