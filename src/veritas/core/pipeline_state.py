"""Pipeline state tracker for resumable pipeline phases.

Persists per-stage status, timestamps, and arbitrary per-stage outputs to
``<output_dir>/.veritas/pipeline_state.json``. ``ReplicationRunner`` consults
the tracker before each phase so completed phases are skipped on re-run.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional


STATE_DIR = ".veritas"
STATE_FILE = "pipeline_state.json"
STATE_SCHEMA_VERSION = 2


class PipelineState:
    """Tracks pipeline execution state for resumable phases."""

    def __init__(self, output_dir: Path):
        self.output_dir = Path(output_dir)
        self.state_file = self.output_dir / STATE_DIR / STATE_FILE
        self.state_file.parent.mkdir(parents=True, exist_ok=True)

        if self.state_file.exists():
            with open(self.state_file, 'r', encoding='utf-8') as f:
                self.state = json.load(f)
            self._enforce_schema_version()
        else:
            self.state = {
                'schema_version': STATE_SCHEMA_VERSION,
                'created_at': datetime.now().isoformat(),
                'inputs': None,
                'config': None,
                'stages': {},
                'current_stage': None,
                'completed': False,
            }
            self._save()

    def _save(self) -> None:
        with open(self.state_file, 'w', encoding='utf-8') as f:
            json.dump(self.state, f, indent=2)

    def _enforce_schema_version(self) -> None:
        """Raise a clear-message error if the loaded state predates this veritas version.

        The pre-refactor pipeline produced state files without a ``schema_version``
        field (or with version < 2). Output filenames and subdirectory layout
        changed; reusing those artifacts would mix old evaluation artifacts
        with new per-claim verdicts. Force the user to ``--restart`` rather
        than silently producing wrong output.
        """
        v = self.state.get('schema_version')
        if v is None or v < STATE_SCHEMA_VERSION:
            raise RuntimeError(
                f"Pipeline state file at {self.state_file} predates this version "
                f"of veritas (schema_version={v!r}, expected >={STATE_SCHEMA_VERSION}). "
                f"The output layout has changed since this state was recorded. "
                f"Pass --restart to discard the state file and run the pipeline fresh."
            )

    # -- Stage transitions ---------------------------------------------------

    def start_stage(self, name: str) -> None:
        self.state['current_stage'] = name
        self.state['stages'][name] = {
            'status': 'in_progress',
            'started_at': datetime.now().isoformat(),
            'completed_at': None,
            'success': None,
            'outputs': {},
        }
        self._save()

    def complete_stage(
        self,
        name: str,
        success: bool,
        outputs: Optional[Dict[str, Any]] = None,
    ) -> None:
        if name not in self.state['stages']:
            self.state['stages'][name] = {}

        self.state['stages'][name].update({
            'status': 'completed' if success else 'failed',
            'completed_at': datetime.now().isoformat(),
            'success': success,
            # preserve outputs accumulated via update_stage_outputs (e.g. completed_claims) when caller passes outputs=None
            'outputs': outputs if outputs is not None
            else self.state['stages'][name].get('outputs', {}),
        })
        self.state['current_stage'] = None
        self._save()

    def update_stage_outputs(self, name: str, outputs: Dict[str, Any]) -> None:
        """Merge outputs into a stage that's still in_progress.

        Used for per-claim sub-completion in the ``verify`` stage —
        each completed claim is appended to the stage's outputs without
        marking the whole stage complete.
        """
        if name not in self.state['stages']:
            self.state['stages'][name] = {
                'status': 'in_progress',
                'started_at': datetime.now().isoformat(),
                'completed_at': None,
                'success': None,
                'outputs': {},
            }
        existing = self.state['stages'][name].setdefault('outputs', {})
        existing.update(outputs)
        self._save()

    def mark_completed(self) -> None:
        self.state['completed'] = True
        self.state['completed_at'] = datetime.now().isoformat()
        self._save()

    def invalidate_stages(self, stages: List[str]) -> None:
        """Drop named stages from state so they re-run on the next invocation.

        Also clears the top-level ``completed`` flag if any stage was invalidated,
        since the run is no longer fully done.
        """
        invalidated = False
        for name in stages:
            if name in self.state['stages']:
                del self.state['stages'][name]
                invalidated = True
        if invalidated:
            self.state['completed'] = False
            self.state['completed_at'] = None
        self.state['current_stage'] = None
        self._save()

    # -- Stage queries -------------------------------------------------------

    def get_stage_status(self, name: str) -> Optional[str]:
        return self.state['stages'].get(name, {}).get('status')

    def is_stage_completed(self, name: str) -> bool:
        stage = self.state['stages'].get(name, {})
        return stage.get('status') == 'completed' and stage.get('success', False)

    def get_stage_outputs(self, name: str) -> Dict[str, Any]:
        return self.state['stages'].get(name, {}).get('outputs', {}) or {}

    # -- Input fingerprinting ------------------------------------------------

    def record_inputs(
        self,
        repo_path: Path,
        paper_path: Optional[Path],
    ) -> None:
        """Stash inputs into ``state['inputs']``. Also called to refresh after invalidation."""
        self.state['inputs'] = {
            'repo_path': str(Path(repo_path).resolve()),
            'paper_path': str(Path(paper_path).resolve()) if paper_path else None,
            'paper_sha256': _sha256_of_file(paper_path) if paper_path else None,
        }
        self._save()

    def detect_input_changes(
        self,
        repo_path: Path,
        paper_path: Optional[Path],
    ) -> List[str]:
        """Return names of input fields that differ from the recorded run.

        Returns an empty list when no inputs were recorded yet (first run) or
        when everything matches. Field names are ``repo_path``, ``paper_path``,
        and ``paper_sha256``; the caller handles user-facing messaging and
        stage invalidation.
        """
        recorded = self.state.get('inputs')
        if recorded is None:
            return []

        current_repo = str(Path(repo_path).resolve())
        current_paper = str(Path(paper_path).resolve()) if paper_path else None
        current_sha = _sha256_of_file(paper_path) if paper_path else None

        changed = []
        if recorded.get('repo_path') != current_repo:
            changed.append('repo_path')
        if recorded.get('paper_path') != current_paper:
            changed.append('paper_path')
        if recorded.get('paper_sha256') != current_sha:
            changed.append('paper_sha256')
        return changed

    # -- Config fingerprinting ----------------------------------------------

    def record_config(self, config: Dict[str, Any]) -> None:
        """Stash a config fingerprint into ``state['config']``.

        Also called to refresh after invalidation so the next resume compares
        against the new baseline.
        """
        self.state['config'] = dict(config)
        self._save()

    def detect_config_changes(self, current: Dict[str, Any]) -> List[str]:
        """Return names of config fields that differ from the recorded run.

        Returns an empty list when no config was recorded yet (first run or
        a state file that predates config tracking) or when everything matches.
        Only fields present in ``current`` are compared; recorded-only fields
        are ignored so the schema can grow without breaking older states.
        """
        recorded = self.state.get('config') or {}
        return [k for k in current if recorded.get(k) != current.get(k)]


def _sha256_of_file(path: Optional[Path]) -> Optional[str]:
    if path is None:
        return None
    p = Path(path)
    if not p.exists():
        return None
    h = hashlib.sha256()
    with open(p, 'rb') as f:
        for chunk in iter(lambda: f.read(65536), b''):
            h.update(chunk)
    return h.hexdigest()
