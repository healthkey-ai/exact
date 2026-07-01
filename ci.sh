#!/bin/sh

export MALLOC_NANO_ZERO=0

# Use a test-specific env file that has no external DB URLs,
# so Django's load_dotenv() doesn't route queries to the remote DBs.
export DOTENV_PATH=.env.test

python -m pytest
