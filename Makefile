.PHONY: help run dry-run force test clean

PYTHON ?= python3

help:
	@echo "Antigravity Conversation Fix"
	@echo ""
	@echo "Available commands:"
	@echo "  make run        - Run the conversation fix interactively"
	@echo "  make dry-run    - Simulate the fix without modifying databases"
	@echo "  make force      - Force run even if Antigravity is open"
	@echo "  make test       - Run test suite"
	@echo "  make clean      - Remove temporary backups and cache"

run:
	$(PYTHON) rebuild_conversations.py

dry-run:
	$(PYTHON) rebuild_conversations.py --dry-run

force:
	$(PYTHON) rebuild_conversations.py --force

test:
	$(PYTHON) -m unittest discover -s tests -v

clean:
	rm -f trajectorySummaries_backup_*.txt
	rm -rf __pycache__ tests/__pycache__
