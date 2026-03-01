.PHONY: all clean csharp python variant docx install lint import import-force

PYTHON        := .venv/bin/python3
SCRIPT        := generate.py
BUILD         := build
THEME         ?= classic

# Build all variants
all:
	$(PYTHON) $(SCRIPT) --theme $(THEME)

# Build a single variant: make variant V=csharp
variant:
	$(PYTHON) $(SCRIPT) --variant $(V) --theme $(THEME)

# Individual named targets for convenience
csharp:
	$(PYTHON) $(SCRIPT) --variant csharp --theme $(THEME)

python:
	$(PYTHON) $(SCRIPT) --variant python --theme $(THEME)

# Build DOCX output for all variants (uses reference.docx automatically if present)
docx:
	$(PYTHON) $(SCRIPT) --format docx --theme $(THEME)

# Install Python dependencies
install:
	pip install -r requirements.txt

# Lint the YAML source
lint:
	$(PYTHON) -c "import yaml, sys; yaml.safe_load(open('resume.yaml'))" && echo "YAML OK"

# Remove all generated artifacts
clean:
	rm -rf $(BUILD)

## Generate resume.yaml from resume.docx (or .pdf/.md if docx not found)
## Skips if resume.yaml is already up to date. Use force=1 to override.
import:
	$(PYTHON) scripts/build_yaml_from_source.py $(if $(force),--force,)

## Force regenerate resume.yaml even if source hasn't changed
import-force:
	$(PYTHON) scripts/build_yaml_from_source.py --force
