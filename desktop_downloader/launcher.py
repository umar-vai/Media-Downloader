from __future__ import annotations

import customtkinter as ctk

from app import (
    BORDER,
    MUTED,
    PURPLE,
    PURPLE_HOVER,
    SURFACE_2,
    DownloaderApp,
)


class MediaDownloaderApp(DownloaderApp):
    """Desktop app with an explicit reset-to-next-job workflow."""

    def _find_button_by_text(self, root, text: str):
        for child in root.winfo_children():
            if isinstance(child, ctk.CTkButton):
                try:
                    if child.cget("text") == text:
                        return child
                except Exception:
                    pass
            found = self._find_button_by_text(child, text)
            if found is not None:
                return found
        return None

    def _build_download_card(self) -> None:
        super()._build_download_card()
        button = self._find_button_by_text(self.content, "Clear workspace")
        self.reset_session_button = button
        if button is not None:
            button.configure(
                text="Reset for next download",
                command=self.reset_for_next_download,
                fg_color="transparent",
                hover_color=SURFACE_2,
                border_color=BORDER,
                text_color=MUTED,
            )

    def _set_busy(self, busy: bool) -> None:
        super()._set_busy(busy)
        button = getattr(self, "reset_session_button", None)
        if button is not None:
            button.configure(state="disabled" if busy else "normal")

    def _set_status(self, text: str, kind: str = "ready") -> None:
        super()._set_status(text, kind)
        button = getattr(self, "reset_session_button", None)
        if button is None:
            return
        if kind == "success":
            button.configure(
                text="Reset for next download",
                state="normal",
                fg_color=PURPLE,
                hover_color=PURPLE_HOVER,
                text_color="#FFFFFF",
            )
        elif kind in {"ready", "error", "cancelled"}:
            button.configure(
                text="Reset for next download",
                fg_color="transparent",
                hover_color=SURFACE_2,
                border_color=BORDER,
                text_color=MUTED,
            )

    def reset_for_next_download(self) -> None:
        """Clear the completed/failed media session while preserving user preferences."""
        self.clear_form()
        self.last_file = None
        self.open_file_button.configure(state="disabled")

        self.mode_var.set("Video")
        self.video_quality_var.set("720p")
        self.audio_format_var.set("MP3")
        self.audio_quality_var.set("192")
        self._sync_mode("Video")

        button = getattr(self, "reset_session_button", None)
        if button is not None:
            button.configure(
                text="Reset for next download",
                state="normal",
                fg_color="transparent",
                hover_color=SURFACE_2,
                border_color=BORDER,
                text_color=MUTED,
            )

        self._set_status("Ready for the next download", "ready")
        self.url_entry.focus_set()


if __name__ == "__main__":
    MediaDownloaderApp().mainloop()
