from pathlib import Path

APP = Path("desktop_downloader/app.py")
text = APP.read_text(encoding="utf-8")

old = '''        except Exception as exc:
            self.events.put(("error", f"Could not read this media link after trying the available connection paths. Public links work best; private or login-required content is not supported.\\n\\n{exc}"))

    def download(self) -> None:
'''
new = '''        except Exception as exc:
            self.events.put(("error", f"Could not read this media link after trying the available connection paths. Public links work best; private or login-required content is not supported.\\n\\n{exc}"))
        finally:
            # Always release the UI even if a platform extractor exits through an
            # unusual path after media metadata has already been queued.
            self.events.put(("analysis_finished", None))

    def download(self) -> None:
'''
if old not in text:
    raise SystemExit("analysis worker marker not found")
text = text.replace(old, new, 1)

old = '''        except Exception as exc:
            self.events.put(("error", str(exc)))

    def _apply_thumbnail(self, raw: bytes | None) -> None:
'''
new = '''        except Exception as exc:
            self.events.put(("error", str(exc)))
        finally:
            # A final state event prevents a completed/failed worker from leaving
            # the Download button disabled if another UI event raises unexpectedly.
            self.events.put(("download_finished", None))

    def _apply_thumbnail(self, raw: bytes | None) -> None:
'''
if old not in text:
    raise SystemExit("download worker marker not found")
text = text.replace(old, new, 1)

old = '''                elif kind == "error":
                    self._set_status("Download failed", "error")
                    self.speed_label.configure(text="")
                    self._set_busy(False)
                    messagebox.showerror(APP_NAME, str(payload))
                elif kind == "update_available":
'''
new = '''                elif kind == "error":
                    self._set_status("Download failed", "error")
                    self.speed_label.configure(text="")
                    self._set_busy(False)
                    messagebox.showerror(APP_NAME, str(payload))
                elif kind == "analysis_finished":
                    # Metadata may have been rendered before the worker fully exits.
                    # Always release the busy lock after the analysis thread ends.
                    self._set_busy(False)
                    if self.current_info is not None:
                        platform = detect_platform(self.url_var.get().strip())
                        label = platform_name(platform or "media")
                        self._set_status(f"{label} media information loaded", "ready")
                elif kind == "download_finished":
                    # done/error normally releases the lock first; this is an
                    # idempotent safety net for repeated back-to-back downloads.
                    self._set_busy(False)
                elif kind == "update_available":
'''
if old not in text:
    raise SystemExit("event handler marker not found")
text = text.replace(old, new, 1)

old = '''        except queue.Empty:
            pass
        self.after(120, self._drain_events)

    def choose_download_folder(self) -> None:
'''
new = '''        except queue.Empty:
            pass
        except Exception as exc:
            # Never let one malformed/stale UI event permanently stop the event
            # pump or leave controls disabled.
            append_update_log(f"UI event error: {exc}")
            self._set_busy(False)
        finally:
            self.after(120, self._drain_events)

    def choose_download_folder(self) -> None:
'''
if old not in text:
    raise SystemExit("event loop tail marker not found")
text = text.replace(old, new, 1)

old = '''        self._apply_thumbnail(None)
        self._set_status("Ready to download", "ready")

    def _save_update_preferences(self) -> None:
'''
new = '''        self._apply_thumbnail(None)
        self._set_status("Ready to download", "ready")
        self._set_busy(False)

    def _save_update_preferences(self) -> None:
'''
if old not in text:
    raise SystemExit("clear form marker not found")
text = text.replace(old, new, 1)

APP.write_text(text, encoding="utf-8")
print("Applied v2.3.6 repeated-download busy-state fix")
