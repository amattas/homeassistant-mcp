#!/bin/bash

echo "Running MattasMCP Test Suite"
echo "============================="
echo ""

# Run tests with coverage
python -m pytest tests/ \
    --tb=short \
    --cov=services \
    --cov=helpers \
    --cov-report=term-missing \
    --cov-report=html \
    -v
status=$?

# Show summary
echo ""
echo "Test Summary:"
echo "============="
python -m pytest tests/ --co -q | grep "test session" -A 1

echo ""
echo "Coverage report saved to htmlcov/index.html"

# Exit with pytest's status so CI fails on test or collection errors.
exit "$status"
