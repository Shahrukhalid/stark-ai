

def test_unwritable_workspace_falls_back_to_the_data_folder(settings, monkeypatch):
    """A read-only workspace (e.g. a Docker bind mount owned by another user) must not stop the API from starting."""
    from pathlib import Path

    from stark import video

    real = Path.mkdir
    ws = settings.workspace_roots[0]

    def mkdir(self, *a, **k):
        if ws in self.parents or self == ws:
            raise PermissionError(13, "Permission denied", str(self))
        return real(self, *a, **k)

    monkeypatch.setattr(Path, "mkdir", mkdir)
    d = video.folder(settings)
    assert d == Path(settings.data_dir) / video.VIDEO_DIR and d.is_dir()
