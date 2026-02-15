# Threading and Interpreter Mode Fixes

## Summary

Fixed critical threading crashes and race conditions in the Python plugin, enabling robust support for modern Python packages (FastAPI, asyncio, threading) when compiled in single-interpreter mode.

## Problems Fixed

### 1. Optional Parameter Format String Bug (CRITICAL)
**Problem**: `w_unpack()` failed when format strings had optional parameters (`"ss|s"`)
- Strict `strcmp()` couldn't handle `|` separator syntax
- All `vh.GetConfig()` calls with 2 args were failing
- Returned 0 on mismatch, breaking all optional param functions

**Fix**: Parse `|` separator, validate arg count in range, skip `|` in format loop

### 2. NULL Pointer Race Condition (CRITICAL) 
**Problem**: Missing optional parameters left garbage in pointer variables
- `va_arg()` consumed pointers but didn't write NULL
- Random segfaults from dereferencing invalid addresses
- Major source of race conditions in stress tests

**Fix**: Write NULL/0 to all missing optional parameter pointers:
```cpp
if (arg_idx >= actual_args) {
    *va_arg(ap, char **) = NULL;  // Critical fix
}
```

### 3. PyThreadState Swap Crash (Threading)
**Problem**: State swapping in single-interpreter mode caused crashes
- In single-mode, all scripts share same `PyThreadState` (main_state)
- Background threads (FastAPI/uvicorn) running concurrently
- `PyThreadState_Get()` could return NULL from background thread
- Swapping to/from NULL → segfault in `_PyEval_EvalFrameDefault`
- Crashed after ~10 seconds under concurrent load

**Fix**: Only swap states in sub-interpreter mode:
```cpp
#ifndef PYTHON_USE_SINGLE_INTERPRETER
    PyThreadState *old_state = PyThreadState_Get();
    PyThreadState_Swap(script->state);
#endif
// ... Python call ...
#ifndef PYTHON_USE_SINGLE_INTERPRETER
    PyThreadState_Swap(old_state);
#endif
```

### 4. Destructor MySQL Crash
**Problem**: Destructor calling `SetConfig()` after MySQL teardown
- Config system destroyed before plugin destructor
- Segfault in `cConfMySQL::ufEqual::operator()`

**Fix**: Remove `SetConfig()` call from destructor (log level not critical during shutdown)

## Test Results

All 5 stress tests now pass consistently:

```
Test 1 (LoadScript):                    ✅ PASS (693 ms)
Test 2 (StartApiServer):                ✅ PASS (2009 ms)
Test 3 (ConcurrentMessagesAndApiCalls): ✅ PASS (17222 ms, 5000 messages)
Test 4 (RapidCommandProcessing):        ✅ PASS (12037 ms, 600 commands)
Test 5 (MemoryLeakDetection):           ✅ PASS (2245 ms, 544 KB growth)
Destructor:                              ✅ Clean exit
```

**Concurrent Load Testing:**
- 600 rapid commands processed safely
- 5000 messages with 3 threads simultaneously  
- FastAPI server handling HTTP requests during hub operations
- No crashes, clean shutdown
- Total memory growth: 544 KB (negligible)

## Documentation Updates

### hub_api.py
- Added comprehensive compilation requirement section
- Explained PyO3/Rust C extension incompatibility
- Documented single vs sub-interpreter trade-offs
- Added thread safety note with cache pattern reference

### scripts/README.md
- New section: "Interpreter Modes and Threading"
- Compilation mode comparison matrix
- Package compatibility issues (PyO3, global C state, cached imports)
- Decision matrix for mode selection
- Real-world threading crash case study
- Debugging tips for threading issues

### plugins/python/README.md
- Updated Overview with interpreter mode summary
- New "Python Environment" section with detailed mode comparison
- Package compatibility reference table
- Testing procedure for sub-interpreter compatibility
- Expanded "Threading Model" section with:
  - Sub-interpreter vs single-interpreter threading behavior
  - Critical threading rules
  - Real-world PyThreadState_Swap bug case study
  - Safe patterns and anti-patterns

## Key Learnings

### Single-Interpreter Mode
**Required for modern Python packages:**
- FastAPI, Pydantic, uvicorn (PyO3/Rust)
- numpy, pandas, scipy (global C state)
- torch, tensorflow (static initialization)
- Any package with C extensions using global state

**Benefits:**
- All packages work
- Threading and asyncio fully supported
- Better performance (no state swapping overhead)

**Trade-offs:**
- Scripts share global namespace (use unique names!)
- Memory leaks affect all scripts

### Sub-Interpreter Mode
**Best for:**
- Simple event hooks
- Pure Python scripts
- Multi-user development environments
- Script isolation requirements

**Limitations:**
- Many modern packages don't work
- Threading has cleanup issues
- asyncio features limited

### Thread Safety
**Rule**: NEVER call `vh.*` from background threads
- Use queue pattern (background → queue → OnTimer → vh call)
- Use cache pattern (OnTimer updates cache → background reads)

**In Single-Interpreter Mode:**
- No state swapping needed (all scripts share main_state)
- State swapping is actively harmful with background threads
- GIL acquisition still required

## Files Modified

- `plugins/python/wrapper.cpp`: Optional param parsing, state swap guards
- `plugins/python/cpipython.cpp`: Removed destructor SetConfig call
- `plugins/python/scripts/hub_api.py`: Documentation updates
- `plugins/python/scripts/README.md`: Threading and mode documentation
- `plugins/python/README.md`: Comprehensive mode and threading guide

## Compilation

**For modern Python packages (FastAPI, numpy, etc.):**
```bash
cmake -DPYTHON_USE_SINGLE_INTERPRETER=ON ..
make
```

**For script isolation (simple scripts only):**
```bash
cmake ..
make
```

## Future Improvements

- Command queue in C++ for thread-safe vh module calls
- Per-thread hub contexts
- Mutex protection in C++ API
- Python 3.12+ per-interpreter GIL (PEP 684) support
