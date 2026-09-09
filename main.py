# main.py

from __future__ import annotations
import sys
import os
import math
import time
import json # Added for notes
import shutil

from pathlib import Path
from datetime import datetime
from typing import Optional, List, Dict, Union, Any

from PySide6.QtCore import Qt, QTimer, QSettings
from PySide6.QtGui import QAction, QActionGroup, QCloseEvent, QKeySequence, QPalette, QColor, QFont, QIcon
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QStatusBar, QLabel, QInputDialog, QMessageBox,
    QDockWidget, QWidget, QVBoxLayout, QTableWidget, QTableWidgetItem,
    QAbstractItemView, QLineEdit, QToolBar, QFileDialog, QDialog, QPushButton,
    QFrame, QHBoxLayout, QGridLayout, QGroupBox, QScrollArea,
    QProgressBar, QTabWidget, QStackedWidget
)
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer

from copaiba import OtoFile, OtoEntry
from waveform_widget import WaveformWidget
from presets import PRESETS, Preset
from spectrogram_config_widget import SpectrogramConfigWidget

# Discord Rich Presence (opcional)
try:
    from discord_rpc import init_discord_rpc, get_discord_rpc, shutdown_discord_rpc
    DISCORD_RPC_AVAILABLE = True
except ImportError:
    DISCORD_RPC_AVAILABLE = False

# Refactored modules imports
from core.audio_player import AudioPlayer
from core.logger import logger, setup_logger
from dialogs.exit_dialog import AdvancedExitDialog
from dialogs.plugin_manager import PluginManagerDialog
from dialogs.keybinding_config import KeybindingConfigDialog, DEFAULT_KEYBINDINGS
from dialogs.settings_dialog import SettingsDialog
from widgets.preset_config import PresetConfigWidget
from views.menu_builder import MenuBuilder
from controllers.project_controller import ProjectController
from controllers.audio_controller import AudioController
from controllers.table_controller import TableController
from core.types import CellEdit, RowEdit, ProjectData
from dialogs.batch_edit_dialog import BatchEditDialog
from dialogs.cheatsheet_dialog import CheatsheetDialog
from core.project_session import ProjectSession

# Configura logger inicial
setup_logger("copaiba")

# Audio imports
try:
    import numpy as np
    import sounddevice as sd
    import wave

    SOUNDDEVICE_AVAILABLE = True
except ImportError:
    SOUNDDEVICE_AVAILABLE = False

# GPU imports - usa se disponível, senão fallback para CPU
try:
    from backend_gpu import get_gpu_backend, gpu_available, gpu_enabled, enable_gpu, disable_gpu, get_gpu_info, \
        get_device_name, GPUVendor

    GPU_BACKEND_AVAILABLE = True
except ImportError:
    GPU_BACKEND_AVAILABLE = False


    def gpu_available():
        return False


    def gpu_enabled():
        return False


    def enable_gpu():
        return False


    def disable_gpu():
        pass


    def get_device_name():
        return "CPU"


    def get_gpu_info():
        return None


# Plugins são carregados via lazy import quando abertos
PLUGINS_AVAILABLE = True  # Assume disponível, verifica no momento de uso


class MainWindow(QMainWindow):
    COL_FAV = 0
    COL_FILENAME = 1
    COL_ALIAS = 2
    COL_OFFSET = 3
    COL_OVERLAP = 4
    COL_PREUTTER = 5
    COL_CONSONANT = 6
    COL_CUTOFF = 7
    COL_NOTES = 8 # Nova coluna
    
    # Cabeçalhos das colunas
    COLUMN_HEADERS = [
        "✓", "Arquivo .wav", "Alias (fonema)", "Offset", "Overlap", 
        "Preutterance", "Consonant", "Cutoff", "Anotações"
    ]

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Copaiba Lexikon | 2026.4 v120.1")
        self.resize(1600, 900)
        
        # Configura ícone da janela
        if getattr(sys, 'frozen', False):
            # Executando como .exe
            icon_path = Path(sys.executable).parent / 'favicon.ico'
        else:
            # Executando como script
            icon_path = Path(__file__).parent / 'favicon.ico'
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))
        
        self.settings = QSettings("POMAR LTS", "Copaiba")

        self.sessions: list[ProjectSession] = []
        self.current_session_index: int = -1

        self._encoding: str = "auto"
        self._updating_from_code: bool = False
        self._in_undo_redo: bool = False
        self._keep_zoom_on_alias_change: bool = True
        self._project_file: Path | None = None
        self._last_saved_time: datetime | None = None
        self._srp_enabled: bool = False
        self._srna_enabled: bool = False
        self._open_plugin_dialogs: list = []  # Keep references to prevent GC
        self._wave_theme_index: int = 0
        self._session_start_time = datetime.now()

        if SOUNDDEVICE_AVAILABLE:
            self._audio_player = AudioPlayer()
        else:
            self._audio_player = None
            self._audio_output = QAudioOutput(self)
            self._player = QMediaPlayer(self)
            self._player.setAudioOutput(self._audio_output)

        self._segment_timer: QTimer | None = None
        self._waveform_timer = QTimer(self)
        self._waveform_timer.setSingleShot(True)
        self._waveform_timer.timeout.connect(self._perform_waveform_load)
        self._auto_save_timer: QTimer | None = None
        self._auto_save_interval: int = 300
        self._backup_count: int = 5

        # Timer visual de auto-save
        self._seconds_until_save = 0
        self._auto_save_countdown_timer = QTimer(self)
        self._auto_save_countdown_timer.timeout.connect(self._update_auto_save_label)

        # Timer de sessão
        self._session_timer = QTimer(self)
        self._session_timer.timeout.connect(self._update_session_time)
        self._session_timer.start(60000)  # Atualiza a cada minuto

        self._setup_ui()
        
        # Controllers refatorados
        self.project_ctrl = ProjectController(self)
        self.audio_ctrl = AudioController(self)
        self.table_ctrl = TableController(self)
        
        # Synthesis Test
        from synthesis_test import SynthesisTest
        self.synthesis_test = SynthesisTest(self.settings)
        
        # Usa MenuBuilder para criar ações, menus e toolbar
        menu_builder = MenuBuilder(self)
        menu_builder.create_all()
        
        self._setup_status_bar()
        
        # --- TAB INITIALIZATION ---
        # We need an initial empty session, but it must be added after acts exist
        self._add_new_session("Novo")
        
        self._update_undo_redo_actions()
        self._load_settings()
        
        # --- NOVO: Restaura última sessão após um pequeno delay (permite UI carregar) ---
        QTimer.singleShot(300, self._restore_last_session)
        
        # Inicializa Discord Rich Presence de forma assíncrona (não bloqueia UI)
        if DISCORD_RPC_AVAILABLE:
            QTimer.singleShot(500, init_discord_rpc)  # Conecta após 500ms

    @property
    def current_session(self) -> ProjectSession | None:
        if hasattr(self, 'sessions') and self.sessions and self.current_session_index >= 0:
            return self.sessions[self.current_session_index]
        return None

    @property
    def table(self): return self.current_session.table if self.current_session else None

    @property
    def filter_bar(self): return self.current_session.filter_bar if self.current_session else None

    @property
    def waveform(self): return self.current_session.waveform if self.current_session else None

    @property
    def _oto(self): return self.current_session.oto if self.current_session else None

    @_oto.setter
    def _oto(self, val):
        if self.current_session: self.current_session.oto = val

    @property
    def _current_path(self): return self.current_session.current_path if self.current_session else None

    @_current_path.setter
    def _current_path(self, val):
        if self.current_session: self.current_session.current_path = val

    @property
    def _voicebank_dir(self): return self.current_session.voicebank_dir if self.current_session else None

    @_voicebank_dir.setter
    def _voicebank_dir(self, val):
        if self.current_session: self.current_session.voicebank_dir = val

    @property
    def _dirty(self): return self.current_session.dirty if self.current_session else False

    @_dirty.setter
    def _dirty(self, val):
        if self.current_session: self.current_session.dirty = val

    @property
    def _undo_stack(self): return self.current_session.undo_stack if self.current_session else []

    @property
    def _redo_stack(self): return self.current_session.redo_stack if self.current_session else []

    @property
    def _completed_aliases(self): return self.current_session.completed_aliases if self.current_session else set()

    @property
    def _notes_data(self): return self.current_session.notes_data if self.current_session else {}

    @_notes_data.setter
    def _notes_data(self, val):
        if self.current_session: self.current_session.notes_data = val

    @property
    def _last_selected_row(self): return self.current_session.last_selected_row if self.current_session else None

    @_last_selected_row.setter
    def _last_selected_row(self, val):
        if self.current_session: self.current_session.last_selected_row = val

    @property
    def _clipboard_data(self): return self.current_session.clipboard_data if self.current_session else []

    @_clipboard_data.setter
    def _clipboard_data(self, val):
        if self.current_session: self.current_session.clipboard_data = val

    @property
    def _clipboard_cols(self): return self.current_session.clipboard_cols if self.current_session else []

    @_clipboard_cols.setter
    def _clipboard_cols(self, val):
        if self.current_session: self.current_session.clipboard_cols = val

    @property
    def _last_shift_click_row(self): return self.current_session.last_shift_click_row if self.current_session else None

    @_last_shift_click_row.setter
    def _last_shift_click_row(self, val):
        if self.current_session: self.current_session.last_shift_click_row = val

    def _setup_ui(self):
        self.tab_widget = QTabWidget(self)
        self.tab_widget.setTabPosition(QTabWidget.TabPosition.North)
        self.tab_widget.setTabsClosable(True)
        # self.tab_widget.setMaximumHeight(32)
        self.tab_widget.tabCloseRequested.connect(self._on_tab_close_requested)
        self.tab_widget.currentChanged.connect(self._on_tab_changed)
        
        self.stacked_params = QStackedWidget(self)
        self.stacked_wave = QStackedWidget(self)

        self.preset_config = PresetConfigWidget(self)
        self.preset_config.presetsChanged.connect(self._on_presets_changed)
        self.spectrogram_config = SpectrogramConfigWidget(self)

        self.spectrogram_config.gammaChanged.connect(
            lambda v: self.waveform._spectrogram_widget.set_gamma(v) if self.waveform else None)
        self.spectrogram_config.contrastChanged.connect(
            lambda v: self.waveform._spectrogram_widget.set_contrast(v) if self.waveform else None)
        self.spectrogram_config.colormapChanged.connect(
            lambda v: self.waveform._spectrogram_widget.set_colormap(v) if self.waveform else None)
        self.spectrogram_config.freqRangeChanged.connect(
            lambda min_f, max_f: self.waveform._spectrogram_widget.set_freq_range(min_f, max_f) if self.waveform else None)
        self.spectrogram_config.gpuChanged.connect(
            lambda g: self.waveform._spectrogram_widget.set_use_gpu(g) if self.waveform else None)
        
        self.spectrogram_config.fftParamsChanged.connect(
            lambda n, h, w: self.waveform._spectrogram_widget.set_fft_params(n, h, w) if self.waveform else None)

        if hasattr(self.spectrogram_config, 'colorBackgroundChanged'):
            self.spectrogram_config.colorBackgroundChanged.connect(
                lambda c: self.waveform._spectrogram_widget.set_background_color(c) if self.waveform else None)
        if hasattr(self.spectrogram_config, 'colorSpectrumChanged'):
            self.spectrogram_config.colorSpectrumChanged.connect(
                lambda c: self.waveform._spectrogram_widget.set_spectrum_color(c) if self.waveform else None)

        if GPU_BACKEND_AVAILABLE and gpu_available():
            self.spectrogram_config.set_gpu_available(True)
        else:
            self.spectrogram_config.set_gpu_available(False)

        # DOCK PARAMS (Tabela) - Agora em baixo
        dock_params = QDockWidget("Parâmetros", self)
        dock_params.setWidget(self.stacked_params)
        dock_params.setObjectName("DockParams")
        dock_params.setFeatures(QDockWidget.DockWidgetMovable | QDockWidget.DockWidgetFloatable)
        self.addDockWidget(Qt.BottomDockWidgetArea, dock_params)
        self._dock_params = dock_params

        # DOCK WAVEFORM - Agora em cima
        dock_wave = QDockWidget("Waveform", self)
        dock_wave.setWidget(self.stacked_wave)
        dock_wave.setObjectName("dock_wave") 
        # Permite mover e flutuar (não fechar - essencial para layout)
        dock_wave.setFeatures(QDockWidget.DockWidgetMovable | QDockWidget.DockWidgetFloatable)
        self.addDockWidget(Qt.TopDockWidgetArea, dock_wave)
        self._dock_wave = dock_wave

        scroll_area = QScrollArea()
        scroll_area.setWidget(self.preset_config)
        scroll_area.setWidgetResizable(True)
        scroll_area.setMinimumWidth(300)

        dock_presets = QDockWidget("Configurações de Presets", self)
        dock_presets.setWidget(scroll_area)
        dock_presets.setObjectName("DockPresets")
        dock_presets.setFeatures(
            QDockWidget.DockWidgetMovable | QDockWidget.DockWidgetFloatable | QDockWidget.DockWidgetClosable)
        self.addDockWidget(Qt.RightDockWidgetArea, dock_presets)
        dock_presets.hide()
        self._dock_presets = dock_presets

        scroll_area_spec = QScrollArea()
        scroll_area_spec.setWidget(self.spectrogram_config)
        scroll_area_spec.setWidgetResizable(True)
        scroll_area_spec.setMinimumWidth(320)

        dock_spectrogram_config = QDockWidget("Configurações do Espectrograma", self)
        dock_spectrogram_config.setWidget(scroll_area_spec)
        dock_spectrogram_config.setObjectName("DockSpectrogramConfig")
        dock_spectrogram_config.setFeatures(
            QDockWidget.DockWidgetMovable |
            QDockWidget.DockWidgetFloatable |
            QDockWidget.DockWidgetClosable
        )
        self.addDockWidget(Qt.RightDockWidgetArea, dock_spectrogram_config)
        dock_spectrogram_config.hide()
        self._dock_spectrogram_config = dock_spectrogram_config

        # Distribui espaço inicial [Wave: 40%, Params: 60%]
        self.resizeDocks([dock_wave, dock_params], [300, 500], Qt.Vertical)

        # O TabWidget central será usado para os Mini-Arquivos (sessions)
        self.setCentralWidget(self.tab_widget)

    def _setup_status_bar(self):
        status = QStatusBar(self)
        self.setStatusBar(status)
        self._gpu_status_label = QLabel()
        self._update_gpu_status_label()
        status.addPermanentWidget(self._gpu_status_label)

        self._auto_save_label = QLabel("")
        self._auto_save_label.setStyleSheet("color: #aaa; padding: 0 10px;")
        status.addPermanentWidget(self._auto_save_label)

        # Novo: Último salvamento
        self._last_save_label = QLabel("Salvo: Nunca")
        self._last_save_label.setStyleSheet("padding: 0 10px;")
        status.addPermanentWidget(self._last_save_label)

        # Novo: Timer de sessão
        self._session_time_label = QLabel("Sessão: 0 min")
        self._session_time_label.setStyleSheet("padding: 0 10px;")
        status.addPermanentWidget(self._session_time_label)

        # Container para barra de progresso estilizada
        progress_container = QWidget()
        progress_layout = QHBoxLayout(progress_container)
        progress_layout.setContentsMargins(5, 0, 5, 0)
        progress_layout.setSpacing(6)
        
        # Emoji indicador de progresso
        self._progress_emoji = QLabel("🌱")
        self._progress_emoji.setStyleSheet("font-size: 14px;")
        progress_layout.addWidget(self._progress_emoji)
        
        # Barra de progresso visual premium
        self._progress_bar = QProgressBar()
        self._progress_bar.setMinimum(0)
        self._progress_bar.setMaximum(100)
        self._progress_bar.setValue(0)
        self._progress_bar.setFixedWidth(140)
        self._progress_bar.setFixedHeight(14)
        self._progress_bar.setTextVisible(False)
        self._progress_bar.setStyleSheet("""
            QProgressBar {
                border: none;
                border-radius: 7px;
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #1a1a1a, stop:0.5 #252525, stop:1 #1a1a1a);
                padding: 1px;
            }
            QProgressBar::chunk {
                border-radius: 6px;
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #00c853, stop:0.3 #69f0ae, stop:0.7 #00e676, stop:1 #00c853);
                margin: 1px;
            }
        """)
        progress_layout.addWidget(self._progress_bar)
        
        status.addPermanentWidget(progress_container)

        self._progress_label = QLabel("0/0")
        self._progress_label.setStyleSheet("""
            QLabel {
                color: #888;
                font-size: 11px;
                font-weight: bold;
                padding: 0 8px;
            }
        """)
        status.addPermanentWidget(self._progress_label)
        self._line_indicator_label = QLabel("Linha: -/-")
        status.addPermanentWidget(self._line_indicator_label)

    def _update_gpu_status_label(self):
        """Atualiza o label discreto de status da GPU na barra de status."""
        if GPU_BACKEND_AVAILABLE and gpu_enabled():
            device = get_device_name()
            # Mostra de forma discreta que GPU está ativa
            self._gpu_status_label.setText(f"⚡ {device}")
            self._gpu_status_label.setStyleSheet("color: #4CAF50; font-size: 10px;")
            self._gpu_status_label.setToolTip(f"Aceleração GPU ativa: {device}")
        else:
            # CPU - mostra de forma ainda mais discreta
            self._gpu_status_label.setText("💻")
            self._gpu_status_label.setStyleSheet("color: #666; font-size: 10px;")
            self._gpu_status_label.setToolTip("Usando CPU para processamento")

    def _update_session_time(self):
        diff = datetime.now() - self._session_start_time
        minutes = int(diff.total_seconds() / 60)

        if minutes < 60:
            text = f"Você está configurando há {minutes} minutos"
        else:
            hours = minutes // 60
            mins = minutes % 60
            text = f"Você está configurando há {hours} horas e {mins} minutos"

        self._session_time_label.setText(text)

    def _on_play_segment_requested(self, start_ms: float, end_ms: float):
        """Delega ao AudioController."""
        self.audio_ctrl.on_play_segment_requested(start_ms, end_ms)

    def _play_segment(self):
        """Delega ao AudioController."""
        self.audio_ctrl.play_segment()

    def _play_full_audio(self):
        """Delega ao AudioController."""
        self.audio_ctrl.play_full_audio()

    def _run_synthesis_test(self):
        """
        Executa o teste de síntese no alias atual.
        Atalho: Ctrl+Shift+Space
        """
        # Verifica se resampler está configurado
        resampler_path = self.synthesis_test.get_resampler_path()
        if not resampler_path or not resampler_path.exists():
            self._open_synthesis_config()
            # Se usuário cancelou ou não configurou, aborta
            if not self.synthesis_test.get_resampler_path():
                return
        
        # Obtém linha selecionada
        current_row = self.table.currentRow()
        if current_row < 0:
            self.statusBar().showMessage("Nenhum alias selecionado para teste", 3000)
            return
            
        # Lê dados diretamente da tabela para suportar ordenação/filtro e edições instantâneas
        try:
            filename = self.table.item(current_row, self.COL_FILENAME).text()
            alias = self.table.item(current_row, self.COL_ALIAS).text()
            
            # Helper para ler float da tabela com segurança
            def get_val(col):
                try:
                    return float(self.table.item(current_row, col).text())
                except ValueError:
                    return 0.0

            offset = get_val(self.COL_OFFSET)
            overlap = get_val(self.COL_OVERLAP)
            preutter = get_val(self.COL_PREUTTER)
            consonant = get_val(self.COL_CONSONANT)
            cutoff = get_val(self.COL_CUTOFF)
            
            wav_path = self._voicebank_dir / filename
            
            if not wav_path.exists():
                self.statusBar().showMessage(f"Arquivo WAV não encontrado: {filename}", 3000)
                return

            self.statusBar().showMessage(f"Sintetizando: {alias}...", 0)
            
            from synthesis_test import SynthesisParams
            
            # Cria parâmetros
            params = self.synthesis_test.create_params_from_oto(
                wav_path=wav_path,
                offset=offset,
                overlap=overlap,
                preutter=preutter,
                consonant=consonant,
                cutoff=cutoff
            )

            
            # Executa em thread separada para não travar UI? 
            # Como é rápido (<1s), faremos síncrono por enquanto, mas com processEvents
            from PySide6.QtWidgets import QApplication
            QApplication.processEvents()
            
            success, result = self.synthesis_test.synthesize(params)
            
            if success:
                # Toca o resultado
                self.statusBar().showMessage(f"Tocando resultado síntese...", 2000)
                # Usa AudioPlayer para tocar o arquivo temporário
                if self._audio_player:
                    self._audio_player.play_full(Path(result))
            else:
                QMessageBox.warning(self, "Falha na Síntese", f"Erro: {result}")
                self.statusBar().clearMessage()
                
        except Exception as e:
            QMessageBox.critical(self, "Erro", f"Erro no teste de síntese: {str(e)}")
            self.statusBar().clearMessage()

    def _open_synthesis_config(self):
        """Abre diálogo de configuração do resampler."""
        from synthesis_test import ResamplerConfigDialog
        dialog = ResamplerConfigDialog(self.synthesis_test, self)
        dialog.exec()

    def _open_general_settings(self):
        """Abre o diálogo de configurações gerais."""
        dialog = SettingsDialog(self)
        dialog.exec()



    def _table_mouse_press_event(self, event):
        if event.button() == Qt.LeftButton:
            if event.modifiers() & Qt.AltModifier:
                self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectItems)
                QTableWidget.mousePressEvent(self.table, event)
                return
            
            self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)

            pos = event.position().toPoint()
            item = self.table.itemAt(pos)
            if item:
                row = item.row()
                if event.modifiers() & Qt.ShiftModifier:
                    if self._last_shift_click_row is not None:
                        start_row = min(self._last_shift_click_row, row)
                        end_row = max(self._last_shift_click_row, row)
                        self.table.clearSelection()
                        for r in range(start_row, end_row + 1):
                            for c in range(self.table.columnCount()):
                                if self.table.item(r, c):
                                    self.table.item(r, c).setSelected(True)
                    else:
                        self._last_shift_click_row = row
                        QTableWidget.mousePressEvent(self.table, event)
                else:
                    self._last_shift_click_row = row
                    QTableWidget.mousePressEvent(self.table, event)
            else:
                QTableWidget.mousePressEvent(self.table, event)
        else:
            QTableWidget.mousePressEvent(self.table, event)

    def _table_mouse_release_event(self, event):
        QTableWidget.mouseReleaseEvent(self.table, event)
        self._update_line_indicator()

    def keyPressEvent(self, event):
        if event.matches(QKeySequence.StandardKey.Copy):
            self._copy_selection()
            event.accept()
            return
        elif event.matches(QKeySequence.StandardKey.Paste):
            self._paste_selection()
            event.accept()
            return
        elif event.key() == Qt.Key.Key_Space:
            if event.modifiers() & Qt.ShiftModifier:
                self._play_full_audio()
            else:
                self._play_segment()
            event.accept()
            return
        super().keyPressEvent(event)

    def eventFilter(self, obj, event):
        from PySide6.QtCore import QEvent
        from PySide6.QtWidgets import QTableWidget
        if isinstance(obj, QTableWidget) and event.type() == QEvent.Type.KeyPress:
            if event.matches(QKeySequence.StandardKey.Copy):
                self._copy_selection()
                return True
            elif event.matches(QKeySequence.StandardKey.Paste):
                self._paste_selection()
                return True
        return super().eventFilter(obj, event)

    def _copy_selection(self):
        """Delega ao TableController."""
        self.table_ctrl.copy_selection()

    def _paste_selection(self):
        """Delega ao TableController."""
        self.table_ctrl.paste_selection()

    def _update_line_indicator(self):
        current_row = self.table.currentRow()
        total_rows = self.table.rowCount()
        if current_row >= 0:
            self._line_indicator_label.setText(f"Linha: {current_row + 1}/{total_rows}")
        else:
            self._line_indicator_label.setText("Linha: -/-")

    def _get_last_saved_string(self) -> str:
        if self._last_saved_time is None:
            return "Nunca"
        now = datetime.now()
        diff = now - self._last_saved_time
        if diff.total_seconds() < 60:
            return "Agora há pouco"
        elif diff.total_seconds() < 3600:
            minutes = int(diff.total_seconds() / 60)
            return f"Há {minutes} minuto{'s' if minutes > 1 else ''}"
        elif diff.total_seconds() < 86400:
            hours = int(diff.total_seconds() / 3600)
            return f"Há {hours} hora{'s' if hours > 1 else ''}"
        else:
            return self._last_saved_time.strftime("%d/%m/%Y às %H:%M")

    def _create_backup(self) -> bool:
        if self._current_path is None or not self._current_path.exists():
            return False
        try:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_name = f"{self._current_path.stem}_backup_{timestamp}{self._current_path.suffix}"
            backup_path = self._current_path.parent / backup_name
            shutil.copy2(self._current_path, backup_path)
            backup_pattern = f"{self._current_path.stem}_backup_*{self._current_path.suffix}"
            backups = list(self._current_path.parent.glob(backup_pattern))
            backups.sort(key=lambda x: x.stat().st_mtime, reverse=True)
            for old_backup in backups[10:]:
                try:
                    old_backup.unlink()
                except OSError:
                    pass
            return True
        except Exception as e:
            QMessageBox.warning(self, tr("msg.backup_error_title"), tr("msg.backup_error", error=str(e)))
            return False

    # File menu and Project delegations
    def open_voicebank_folder(self):
        self.project_ctrl.open_voicebank_folder()

    def open_oto(self):
        self.project_ctrl.open_oto()

    def open_project(self):
        self.project_ctrl.open_project()

    def save_project(self):
        self.project_ctrl.save_project()

    def reveal_voicebank(self):
        self.project_ctrl.reveal_voicebank()

    def save_oto(self):
        self.project_ctrl.save_oto()

    def save_oto_as(self):
        self.project_ctrl.save_oto_as()

    def reload_oto(self):
        self.project_ctrl.reload_oto()
    
    def _add_new_session(self, title="Novo"):
        session = ProjectSession(self)
        self.sessions.append(session)
        index = self.tab_widget.addTab(QWidget(), title)
        
        self.stacked_params.addWidget(session.table_container)
        self.stacked_wave.addWidget(session.waveform)
        
        self.tab_widget.setCurrentIndex(index)
        return session

    def _on_tab_changed(self, index):
        if index >= 0 and index < len(self.sessions):
            self.current_session_index = index
            self.stacked_params.setCurrentIndex(index)
            self.stacked_wave.setCurrentIndex(index)
            self._update_title()
            self._update_undo_redo_actions()
            self._update_gpu_status_label()
            if self.waveform:
                 self.waveform.update()

    def _on_tab_close_requested(self, index):
        if index < 0 or index >= len(self.sessions): return
        
        session = self.sessions[index]
        if session.dirty:
            reply = QMessageBox.question(
                self, "Salvar alterações",
                f"O arquivo {self.tab_widget.tabText(index)} foi modificado.\\nDeseja salvar antes de fechar?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No | QMessageBox.StandardButton.Cancel
            )
            if reply == QMessageBox.StandardButton.Yes:
                old_index = self.current_session_index
                self.current_session_index = index
                self.save_oto()
                self.current_session_index = old_index
            elif reply == QMessageBox.StandardButton.Cancel:
                return
        
        self.tab_widget.removeTab(index)
        widget_params = self.stacked_params.widget(index)
        widget_wave = self.stacked_wave.widget(index)
        self.stacked_params.removeWidget(widget_params)
        self.stacked_wave.removeWidget(widget_wave)
        
        self.sessions.pop(index)
        
        if len(self.sessions) == 0:
            self._add_new_session("Novo")
            self.current_session_index = 0
        else:
            if self.current_session_index >= len(self.sessions):
                self.current_session_index = len(self.sessions) - 1
            self.tab_widget.setCurrentIndex(self.current_session_index)
            self._on_tab_changed(self.current_session_index)

    def _load_oto_to_table(self):
        self._updating_from_code = True
        self.table.setRowCount(0)
        self.table.setRowCount(len(self._oto.entries))

        for row, entry in enumerate(self._oto.entries):
            fav_item = QTableWidgetItem()
            fav_item.setFlags(Qt.ItemIsSelectable | Qt.ItemIsEnabled | Qt.ItemIsUserCheckable)
            key = f"{entry.filename}|{entry.alias}"
            if key in self._completed_aliases:
                fav_item.setCheckState(Qt.Checked)
            else:
                fav_item.setCheckState(Qt.Unchecked)
            self.table.setItem(row, self.COL_FAV, fav_item)

            fn_item = QTableWidgetItem(entry.filename)
            fn_item.setFlags(Qt.ItemIsSelectable | Qt.ItemIsEnabled)
            self.table.setItem(row, self.COL_FILENAME, fn_item)

            alias_item = QTableWidgetItem(entry.alias)
            self.table.setItem(row, self.COL_ALIAS, alias_item)

            self.table.setItem(row, self.COL_OFFSET, QTableWidgetItem(str(entry.offset)))
            self.table.setItem(row, self.COL_OVERLAP, QTableWidgetItem(str(entry.overlap)))
            self.table.setItem(row, self.COL_PREUTTER, QTableWidgetItem(str(entry.preutter)))
            self.table.setItem(row, self.COL_CONSONANT, QTableWidgetItem(str(entry.consonant)))
            self.table.setItem(row, self.COL_CUTOFF, QTableWidgetItem(str(entry.cutoff)))
            
            # Nova coluna de anotações
            notes_item = QTableWidgetItem("")
            self.table.setItem(row, self.COL_NOTES, notes_item)

        self._updating_from_code = False
        self._update_undo_redo_actions()
        self._update_row_colors()

    def _restore_notes_to_table(self):
        """Popula a coluna de anotações com os dados carregados."""
        self._updating_from_code = True
        for row in range(self.table.rowCount()):
            filename_item = self.table.item(row, self.COL_FILENAME)
            alias_item = self.table.item(row, self.COL_ALIAS)
            if filename_item and alias_item:
                key = f"{filename_item.text()}|{alias_item.text()}"
                note = self._notes_data.get(key, "")
                notes_table_item = self.table.item(row, self.COL_NOTES)
                if notes_table_item:
                    notes_table_item.setText(note)
                    notes_table_item.setData(Qt.ItemDataRole.UserRole, note) # Store original value for undo
        self._updating_from_code = False

    def save_oto(self):
        """Delega ao ProjectController."""
        self.project_ctrl.save_oto()

    def save_oto_as(self):
        """Delega ao ProjectController."""
        self.project_ctrl.save_oto_as()

    def _sync_table_to_oto(self):
        self._oto.entries.clear()
        for row in range(self.table.rowCount()):
            filename = self.table.item(row, self.COL_FILENAME).text()
            alias = self.table.item(row, self.COL_ALIAS).text()
            offset = int(self.table.item(row, self.COL_OFFSET).text() or "0")
            overlap = int(self.table.item(row, self.COL_OVERLAP).text() or "0")
            preutter = int(self.table.item(row, self.COL_PREUTTER).text() or "0")
            consonant = int(self.table.item(row, self.COL_CONSONANT).text() or "0")
            cutoff = int(self.table.item(row, self.COL_CUTOFF).text() or "0")

            entry = OtoEntry(
                filename=filename,
                alias=alias,
                offset=offset,
                consonant=consonant,
                cutoff=cutoff,
                preutter=preutter,
                overlap=overlap,
            )
            self._oto.entries.append(entry)
        
        # Salva as anotações em um arquivo separado
        if self._current_path:
            notes_path = self._current_path.parent / "notas.copaiba.json"
            try:
                with open(notes_path, 'w', encoding='utf-8') as f:
                    json.dump(self._notes_data, f, ensure_ascii=False, indent=4)
            except Exception as e:
                QMessageBox.warning(self, "Erro ao salvar anotações", f"Não foi possível salvar notas.copaiba.json: {e}")


    def reload_oto(self):
        """Delega ao ProjectController."""
        self.project_ctrl.reload_oto()

    def reveal_voicebank(self):
        """Delega ao ProjectController."""
        self.project_ctrl.reveal_voicebank()

    def open_project(self):
        """Delega ao ProjectController."""
        self.project_ctrl.open_project()

    def _get_projects_folder(self) -> Path:
        """Retorna pasta padrão de projetos em Documentos."""
        docs = Path.home() / "Documents" / "Copaiba Projetos de Voz"
        docs.mkdir(parents=True, exist_ok=True)
        return docs

    def _add_to_recent_projects(self, project_path: Path):
        """Delega ao ProjectController."""
        self.project_ctrl.add_to_recent_projects(project_path)

    def save_project(self):
        """Delega ao ProjectController."""
        self.project_ctrl.save_project()

    def _update_recent_projects_menu(self):
        """Delega ao ProjectController."""
        self.project_ctrl.update_recent_projects_menu()

    def _open_recent_project(self, path_str: str):
        """Delega ao ProjectController."""
        self.project_ctrl.open_recent_project(path_str)

    def _clear_recent_projects(self):
        """Delega ao ProjectController."""
        self.project_ctrl.clear_recent_projects()

    def _on_item_changed(self, item: QTableWidgetItem):
        if self._updating_from_code or self._in_undo_redo:
            return

        row, col = item.row(), item.column()

        if col == self.COL_FAV:
            self._on_complete_toggled(row, item.checkState() == Qt.Checked)
            return

        old_val = item.data(Qt.ItemDataRole.UserRole)
        new_val = item.text()

        if old_val is None:
            old_val = ""

        if old_val != new_val:
            # Block signals to prevent re-triggering itemChanged during correction
            self.table.blockSignals(True)
            try:
                if col in [self.COL_OFFSET, self.COL_OVERLAP, self.COL_PREUTTER, self.COL_CONSONANT, self.COL_CUTOFF]:
                    try:
                        # Allow float values for parameters
                        float(new_val)
                    except ValueError:
                        # Revert if not a valid number
                        self.statusBar().showMessage(f"Valor inválido na linha {row+1}, coluna {col+1}: '{new_val}'. Revertido.", 3000)
                        item.setText(str(old_val))
                        return # Do not push to undo stack if invalid

                # Update notes data if COL_NOTES changed
                if col == self.COL_NOTES:
                    filename_item = self.table.item(row, self.COL_FILENAME)
                    alias_item = self.table.item(row, self.COL_ALIAS)
                    if filename_item and alias_item:
                        key = f"{filename_item.text()}|{alias_item.text()}"
                        self._notes_data[key] = new_val
                    # Notes don't directly affect OTO, but we save them in a separate JSON.
                    # Mark dirty to prompt saving of notes file.
                    self._dirty = True
                elif col == self.COL_ALIAS:
                    # If alias changed, update the key in _notes_data
                    old_filename = self.table.item(row, self.COL_FILENAME).text()
                    old_alias = str(old_val) # Old alias is the old_val for COL_ALIAS
                    old_key = f"{old_filename}|{old_alias}"
                    
                    new_filename = self.table.item(row, self.COL_FILENAME).text()
                    new_alias = new_val
                    new_key = f"{new_filename}|{new_alias}"

                    if old_key in self._notes_data:
                        self._notes_data[new_key] = self._notes_data.pop(old_key)
                    self._dirty = True # Mark dirty to save notes file
            finally:
                self.table.blockSignals(False)

            edit = CellEdit(row, col, str(old_val), new_val)
            self._push_undo([edit])
            item.setData(Qt.ItemDataRole.UserRole, new_val)
            self._dirty = True
            self._update_title()
            self._load_waveform_for_current_row()
            self._update_row_colors(specific_row=row)

    def _on_complete_toggled(self, row: int, completed: bool):
        if row >= len(self._oto.entries):
            return
        entry = self._oto.entries[row]
        key = f"{entry.filename}|{entry.alias}"
        if completed:
            self._completed_aliases.add(key)
        else:
            self._completed_aliases.discard(key)
        self._update_progress(specific_row=row)

    def _on_selection_changed(self):
        self._load_waveform_for_current_row()

    def _on_current_cell_changed(self, current_row, current_col, previous_row, previous_col):
        if current_row != previous_row and current_row >= 0:
            self._load_waveform_for_current_row()

            if previous_row is not None and previous_row >= 0 and previous_row < self.table.rowCount():
                prev_fav_item = self.table.item(previous_row, self.COL_FAV)
                if prev_fav_item and prev_fav_item.checkState() != Qt.Checked:
                    self._updating_from_code = True
                    prev_fav_item.setCheckState(Qt.Checked)
                    self._updating_from_code = False
                    if previous_row < len(self._oto.entries):
                        entry = self._oto.entries[previous_row]
                        key = f"{entry.filename}|{entry.alias}"
                        self._completed_aliases.add(key)
                        self._update_progress(specific_row=previous_row)

        self._update_line_indicator()

    def _load_waveform_for_current_row(self):
        self._waveform_timer.start(100)

    def _perform_waveform_load(self):
        row = self.table.currentRow()
        if row < 0:
            self.waveform.clear()
            return

        # Pega dados da tabela (pode ter sido editado)
        entry = OtoEntry(
            filename=self.table.item(row, self.COL_FILENAME).text(),
            alias=self.table.item(row, self.COL_ALIAS).text(),
            offset=int(self.table.item(row, self.COL_OFFSET).text() or "0"),
            overlap=int(self.table.item(row, self.COL_OVERLAP).text() or "0"),
            preutter=int(self.table.item(row, self.COL_PREUTTER).text() or "0"),
            consonant=int(self.table.item(row, self.COL_CONSONANT).text() or "0"),
            cutoff=int(self.table.item(row, self.COL_CUTOFF).text() or "0"),
        )

        if self._voicebank_dir:
            wav_path = self._voicebank_dir / entry.filename

            # --- CORREÇÃO: Verifica se é ARQUIVO e não DIRETÓRIO ---
            if wav_path.is_file():
                # Determina se deve resetar o zoom ou manter
                reset_zoom = not self.waveform.get_keep_zoom_on_alias_changes()

                self.waveform.show_waveform(wav_path, entry, row, reset_zoom=reset_zoom)
                
                # Pré-carrega próximo e anterior áudio em background
                self._prefetch_adjacent_audio(row)
                
                # Atualiza Discord Rich Presence
                if DISCORD_RPC_AVAILABLE:
                    try:
                        rpc = get_discord_rpc()
                        rpc.set_alias(entry.alias, len(self._completed_aliases), self.table.rowCount())
                    except:
                        pass
            else:
                # Se for diretório ou não existir, limpa a waveform mas mantém os dados da tabela
                self.waveform.clear()
                # Não mostra erro na status bar se for apenas linha vazia/alias duplicate
                if entry.filename.strip():
                    self.statusBar().showMessage(f"Arquivo não acessível: {entry.filename}", 3000)
    
    def _prefetch_adjacent_audio(self, current_row: int):
        """Pré-carrega áudio do próximo e anterior em background."""
        if not self._voicebank_dir:
            return
        
        try:
            from core.waveform_cache import waveform_cache
        except ImportError:
            return  # Cache não disponível
        
        paths_to_prefetch = []
        
        # Próximo
        if current_row + 1 < self.table.rowCount():
            next_item = self.table.item(current_row + 1, self.COL_FILENAME)
            if next_item:
                next_path = self._voicebank_dir / next_item.text()
                if next_path.is_file():
                    paths_to_prefetch.append(next_path)
        
        # Anterior
        if current_row - 1 >= 0:
            prev_item = self.table.item(current_row - 1, self.COL_FILENAME)
            if prev_item:
                prev_path = self._voicebank_dir / prev_item.text()
                if prev_path.is_file():
                    paths_to_prefetch.append(prev_path)
        
        # Pré-carrega em background
        if paths_to_prefetch:
            waveform_cache.prefetch_paths(paths_to_prefetch)

    def _entry_edited_from_waveform(self, row: int, entry: OtoEntry):
        if row < 0 or row >= self.table.rowCount():
            return

        edits = []
        self._updating_from_code = True
        try:
            col_map = {
                self.COL_OFFSET: str(int(entry.offset)),
                self.COL_OVERLAP: str(int(entry.overlap)),
                self.COL_PREUTTER: str(int(entry.preutter)),
                self.COL_CONSONANT: str(int(entry.consonant)),
                self.COL_CUTOFF: str(int(entry.cutoff)),
            }

            for col, new_val in col_map.items():
                item = self.table.item(row, col)
                if item:
                    old_val = item.text()
                    if old_val != new_val:
                        edits.append(CellEdit(row, col, old_val, new_val))
                        item.setText(new_val)
                        item.setData(Qt.ItemDataRole.UserRole, new_val)

        finally:
            self._updating_from_code = False

        if edits:
            self._push_undo(edits)
            self._dirty = True
            self._update_title()
            self._update_row_colors(specific_row=row)

    def apply_preset(self, preset_key: str):
        """Delega ao TableController."""
        self.table_ctrl.apply_preset(preset_key)

    def _push_undo(self, edit: Union[list[CellEdit], RowEdit]):
        self._undo_stack.append(edit)
        self._redo_stack.clear()
        self._update_undo_redo_actions()

    def undo(self):
        """Delega ao TableController."""
        self.table_ctrl.undo()

    def redo(self):
        """Delega ao TableController."""
        self.table_ctrl.redo()

    def _update_undo_redo_actions(self):
        if hasattr(self, 'act_undo'):
            self.act_undo.setEnabled(len(self._undo_stack) > 0)
            self.act_redo.setEnabled(len(self._redo_stack) > 0)
            
    # ============================================================
    # Waveform Proxies (For Menu / Toolbar)
    # ============================================================
    def zoom_in(self):
        if self.waveform: self.waveform.zoom_in()
        
    def zoom_out(self):
        if self.waveform: self.waveform.zoom_out()
        
    def reset_zoom(self):
        if self.waveform: self.waveform.reset_zoom()
        
    def set_snap_enabled(self, checked):
        if self.waveform: self.waveform.set_snap_enabled(checked)
        
    def set_snap_mode(self, mode):
        if self.waveform: self.waveform.set_snap_mode(mode)

    def rename_alias(self):
        """Delega ao TableController."""
        self.table_ctrl.rename_alias()

    def delete_alias(self):
        """Delega ao TableController."""
        self.table_ctrl.delete_alias()

    def duplicate_alias(self):
        """Delega ao TableController."""
        self.table_ctrl.duplicate_alias()

    def _toggle_complete_current(self):
        row = self.table.currentRow()
        if row < 0:
            return

        fav_item = self.table.item(row, self.COL_FAV)
        if fav_item:
            if fav_item.checkState() == Qt.CheckState.Checked:
                fav_item.setCheckState(Qt.CheckState.Unchecked)
            else:
                fav_item.setCheckState(Qt.CheckState.Checked)

    def _step_alias(self, direction: int):
        current_row = self.table.currentRow()
        if current_row < 0:
            return

        new_row = current_row + direction
        if 0 <= new_row < self.table.rowCount():
            self.table.setCurrentCell(new_row, self.table.currentColumn())

    def _handle_waveform_key(self, event) -> bool:
        return False

    def _filter_table(self, text: str):
        """Delega ao TableController."""
        self.table_ctrl.filter_table(text)

    def toggle_minimap(self, checked: bool):
        self.waveform.set_show_minimap(checked)

    def _toggle_spectrogram(self, checked: bool):
        self.waveform.set_show_spectrogram(checked)

    def _toggle_spectrogram_config_dock(self):
        if self._dock_spectrogram_config.isVisible():
            self._dock_spectrogram_config.hide()
        else:
            self._dock_spectrogram_config.show()

    def _toggle_preset_dock(self):
        if self._dock_presets.isVisible():
            self._dock_presets.hide()
        else:
            self._dock_presets.show()

    def _on_presets_changed(self):
        """Atualiza nomes e atalhos das ações de preset quando a config muda."""
        for key, action in self._preset_actions.items():
            # Atualiza nome
            name = self.preset_config.get_preset_name(key)
            action.setText(name)
            
            # Atualiza atalho
            shortcut = self.preset_config.get_preset_shortcut(key)
            if shortcut:
                action.setShortcut(QKeySequence(shortcut))
            else:
                action.setShortcut(QKeySequence())

    # --- NOVAS FUNÇÕES: GPU e Auto-Save ---

    def _enable_gpu_silently(self):
        """
        Ativa GPU silenciosamente nos bastidores.
        Chamado automaticamente no startup quando GPU está disponível.
        """
        if not GPU_BACKEND_AVAILABLE:
            return False

        if gpu_available():
            if enable_gpu():
                # Informa o spectrogram widget para usar GPU
                self.waveform._spectrogram_widget.set_use_gpu(True)
                self._update_gpu_status_label()
                return True

        self._update_gpu_status_label()
        return False

    def _show_gpu_info(self):
        if not GPU_BACKEND_AVAILABLE:
            QMessageBox.information(
                self, "GPU Info",
                "Backend GPU não disponível.\n\nInstale CuPy (NVIDIA) ou PyOpenCL (AMD/Intel) para aceleração GPU."
            )
            return

        info = get_gpu_info()
        status = "✅ Ativada" if gpu_enabled() else "⚠️ Disponível (desativada)"

        msg = f"""
    <b>Informações da GPU</b><br><br>
    <b>Dispositivo:</b> {info}<br>
    <b>Status:</b> {status}<br>
    <b>Vendor:</b> {info.vendor.value.upper()}<br>
    <b>Backend:</b> {info.backend}<br>
    """

        if info.vendor.value != "cpu":
            msg += f"<b>Memória:</b> {info.memory_gb:.1f} GB<br>"
            msg += f"<b>Compute:</b> {info.compute_capability}<br>"

        QMessageBox.information(self, "GPU Info", msg)

    def _toggle_auto_save(self, checked: bool):
        """Ativa Auto-save com diálogo de tempo"""
        if checked:
            # Correção para PySide6: usar argumentos posicionais (value, min, max, step)
            interval, ok = QInputDialog.getInt(
                self, "Configurar Auto-Save", "Intervalo em segundos:",
                300, 30, 3600, 1
            )

            if not ok:
                self.act_toggle_auto_save.setChecked(False)
                return

            self._auto_save_interval = interval
            self._seconds_until_save = interval

            # Inicia Timer de Ação
            if self._auto_save_timer is None:
                self._auto_save_timer = QTimer(self)
                self._auto_save_timer.timeout.connect(self._auto_save)
            self._auto_save_timer.start(interval * 1000)

            # Inicia Timer Visual (1s)
            self._auto_save_countdown_timer.start(1000)
            self._update_auto_save_label()

            self.statusBar().showMessage(f"Auto-save ativado ({interval}s)", 2000)
        else:
            if self._auto_save_timer:
                self._auto_save_timer.stop()
            self._auto_save_countdown_timer.stop()
            self._auto_save_label.setText("")
            self.statusBar().showMessage("Auto-save desativado", 2000)

    def _update_auto_save_label(self):
        """Atualiza o contador regressivo na barra de status"""
        if not self.act_toggle_auto_save.isChecked(): return

        self._seconds_until_save -= 1
        if self._seconds_until_save <= 0:
            self._seconds_until_save = self._auto_save_interval  # Reset visual

        last = "Nunca"
        if self._last_saved_time:
            last = self._last_saved_time.strftime("%H:%M:%S")

        self._auto_save_label.setText(f"Salva em: {self._seconds_until_save}s | Último: {last}")

    def _auto_save(self):
        if self._dirty and self._current_path:
            self.save_oto()

    def _restore_auto_save_silently(self, interval: int):
        """Restaura auto-save silenciosamente sem mostrar diálogo."""
        self._auto_save_interval = interval
        self._seconds_until_save = interval
        
        if self._auto_save_timer is None:
            self._auto_save_timer = QTimer(self)
            self._auto_save_timer.timeout.connect(self._auto_save)
        self._auto_save_timer.start(interval * 1000)
        
        self._auto_save_countdown_timer.start(1000)
        self._update_auto_save_label()

    def _toggle_sector_playback(self, checked: bool):
        """Delega ao AudioController."""
        self.audio_ctrl.toggle_sector_playback(checked)

    def _open_audio_device_dialog(self):
        """Delega ao AudioController."""
        self.audio_ctrl.open_audio_device_dialog()

    def _toggle_normalize_waveform(self, checked: bool):
        """Ativa/Desativa normalização de amplitude da waveform."""
        self.waveform.set_normalize_enabled(checked)
        # Recarrega a waveform para aplicar a mudança
        if self._current_path and self._current_entry:
            row = self.table.currentRow()
            if row >= 0:
                self._load_waveform_for_current_row()
        status = "ativada" if checked else "desativada"
        self.statusBar().showMessage(f"Normalização {status}", 2000)


    def _show_cheatsheet(self):
        """Mostra o diálogo de janela flutuante com o cheatsheet de atalhos."""
        dialog = CheatsheetDialog(self)
        dialog.exec()

    def _open_batch_edit_dialog(self):
        selected_rows = sorted(set(item.row() for item in self.table.selectedItems()))
        if not selected_rows:
            QMessageBox.information(self, "Edição em Lote", "Selecione um ou mais alias para editar em lote.")
            return

        dialog = BatchEditDialog(self, len(selected_rows))
        if dialog.exec() == QDialog.DialogCode.Accepted:
            values = dialog.get_values()
            if not values:
                return

            edits = []
            self._updating_from_code = True
            try:
                for row in selected_rows:
                    if "offset" in values:
                        item = self.table.item(row, self.COL_OFFSET)
                        if item:
                            old_val = item.text()
                            new_val = str(values["offset"])
                            if old_val != new_val:
                                edits.append(CellEdit(row, self.COL_OFFSET, old_val, new_val))
                                item.setText(new_val)
                                item.setData(Qt.ItemDataRole.UserRole, new_val)

                    if "overlap" in values:
                        item = self.table.item(row, self.COL_OVERLAP)
                        if item:
                            old_val = item.text()
                            new_val = str(values["overlap"])
                            if old_val != new_val:
                                edits.append(CellEdit(row, self.COL_OVERLAP, old_val, new_val))
                                item.setText(new_val)
                                item.setData(Qt.ItemDataRole.UserRole, new_val)

                    if "preutter" in values:
                        item = self.table.item(row, self.COL_PREUTTER)
                        if item:
                            old_val = item.text()
                            new_val = str(values["preutter"])
                            if old_val != new_val:
                                edits.append(CellEdit(row, self.COL_PREUTTER, old_val, new_val))
                                item.setText(new_val)
                                item.setData(Qt.ItemDataRole.UserRole, new_val)

                    if "consonant" in values:
                        item = self.table.item(row, self.COL_CONSONANT)
                        if item:
                            old_val = item.text()
                            new_val = str(values["consonant"])
                            if old_val != new_val:
                                edits.append(CellEdit(row, self.COL_CONSONANT, old_val, new_val))
                                item.setText(new_val)
                                item.setData(Qt.ItemDataRole.UserRole, new_val)

                    if "cutoff" in values:
                        item = self.table.item(row, self.COL_CUTOFF)
                        if item:
                            old_val = item.text()
                            new_val = str(values["cutoff"])
                            if old_val != new_val:
                                edits.append(CellEdit(row, self.COL_CUTOFF, old_val, new_val))
                                item.setText(new_val)
                                item.setData(Qt.ItemDataRole.UserRole, new_val)

            finally:
                self._updating_from_code = False

            if edits:
                self._push_undo(edits)
                self._dirty = True
                self._update_title()
                self._load_waveform_for_current_row()
                self.statusBar().showMessage(f"Edição em lote aplicada a {len(selected_rows)} alias(es)", 2000)

    def _set_encoding(self, encoding: str):
        self._encoding = encoding
        
        # Mapeia encoding para action correspondente
        encoding_actions = {
            "auto": self.act_encoding_auto,
            "utf-8": self.act_encoding_utf8,
            "utf-8-sig": self.act_encoding_utf8_bom,
            "cp932": self.act_encoding_cp932,
            "euc-jp": self.act_encoding_eucjp,
            "cp1252": self.act_encoding_ansi,
            "latin-1": self.act_encoding_latin1,
            "gbk": self.act_encoding_gbk,
            "euc-kr": self.act_encoding_euckr,
            # Compatibilidade com configurações antigas
            "mbcs": self.act_encoding_ansi,
        }
        
        if encoding in encoding_actions:
            encoding_actions[encoding].setChecked(True)
        else:
            self.act_encoding_auto.setChecked(True)
        
        # Mostra nome amigável na barra de status
        encoding_names = {
            "auto": "Auto (detectar)",
            "utf-8": "UTF-8",
            "utf-8-sig": "UTF-8 BOM",
            "cp932": "Shift-JIS",
            "euc-jp": "EUC-JP",
            "cp1252": "Windows-1252",
            "latin-1": "Latin-1",
            "gbk": "GBK (Chinês)",
            "euc-kr": "EUC-KR (Coreano)",
        }
        name = encoding_names.get(encoding, encoding)
        self.statusBar().showMessage(f"Encoding: {name}", 2000)

    def _run_pitch_analysis(self):
        """Analisa o áudio do item selecionado usando librosa.pyin (executa em thread separada)."""
        if not self._current_entry or not self._audio_valid:
            return
            
        # Pega offset e cutoff para análise restrita
        try:
            offset = float(self._current_entry.offset)
            cutoff = float(self._current_entry.cutoff)
        except:
            offset = 0
            cutoff = 0
            
        analyzer = PitchAnalyzerPlugin(self)
        # Passa parâmetros adicionais se o plugin suportar
        # O plugin.run() original não aceita args, mas podemos configurar o worker depois?
        # A arquitetura de plugins é simples. Vamos instanciar direto o dialog ou modificar o plugin.
        # Melhor: Modificar o PitchAnalyzerPlugin para aceitar range no construtor ou método set_range.
        
        # Por enquanto, instanciamos. O plugin lê self.mw._current_path e _current_entry.
        # Vamos modificar o plugin para ler self.mw._current_entry e aplicar o crop lá.
        
        dialog = analyzer.get_dialog()
        if dialog:
            dialog.setAttribute(Qt.WA_DeleteOnClose)
            dialog.show()
    
    def _open_plugin_manager(self):
        """Abre o gerenciador de plugins"""
        dialog = PluginManagerDialog(self)
        dialog.exec()

    # --- ZOOM SLIDER SYNC ---
    def update_zoom_slider_from_plot(self, t_min, t_max):
        """Atualiza o slider baseado no range visível (callback do Waveform)."""
        if not self.waveform or not hasattr(self.waveform, '_audio_dur') or self.waveform._audio_dur <= 0:
            return
            
        duration = self.waveform._audio_dur
        width = t_max - t_min
        
        # Inverso da mapping do slider:
        # t = log(width / max_width) / log(min_width / max_width)
        # value = t * 100
        
        min_width = 0.05
        max_width = duration
        
        if width >= max_width: 
            val = 0
        elif width <= min_width:
            val = 100
        else:
            try:
                # Evita log(0) ou divisão por zero
                num = math.log(width / max_width)
                den = math.log(min_width / max_width)
                t = num / den
                val = int(t * 100)
            except:
                val = 50
                
        # Bloqueia sinais para evitar loop
        self.slider_zoom.blockSignals(True)
        self.slider_zoom.setValue(val)
        self.slider_zoom.blockSignals(False)

    def _open_plugin(self, plugin_name: str):
        """Abre o diálogo de um plugin específico (lazy loading)"""
        # Mapeamento de nomes para módulos e classes
        plugin_map = {
            "vv_detector": ("plugins", "VVDetectorPlugin"),
            "pitch": ("plugins", "PitchAnalyzerPlugin"),
            "rename": ("plugins", "BatchRenamePlugin"),
            "sort": ("plugins", "AliasSorterPlugin"),
            "romaji": ("plugins", "RomajiHiraganaPlugin"),
            "duplicates": ("plugins", "DuplicateDetectorPlugin"),
            "consistency": ("plugins", "ConsistencyCheckerPlugin"),
            "oto_merger": ("plugins", "OtoMergerPlugin"),
            "mic_tuner": ("plugins", "MicTunerPlugin"),
            "readme_generator": ("plugins", "ReadmeGeneratorPlugin"),
            "yaml_generator": ("plugins", "YamlGeneratorPlugin"),
        }
        
        if plugin_name not in plugin_map:
            QMessageBox.warning(self, tr("msg.plugin_not_found_title"), tr("msg.plugin_not_found", name=plugin_name))
            return
        
        module_name, class_name = plugin_map[plugin_name]
        
        try:
            # Lazy import do plugin
            import importlib
            module = importlib.import_module(module_name)
            plugin_class = getattr(module, class_name)
            
            plugin = plugin_class(self)
            dialog = plugin.get_dialog()
            if dialog:
                # Non-modal: use show() instead of exec()
                dialog.setAttribute(Qt.WA_DeleteOnClose)
                dialog.destroyed.connect(lambda d=dialog: self._on_plugin_dialog_closed(d))
                self._open_plugin_dialogs.append(dialog)
                dialog.show()
                dialog.raise_()
                dialog.activateWindow()
        except ImportError as e:
            QMessageBox.warning(
                self, tr("msg.plugin_unavailable_title"),
                tr("msg.plugin_unavailable", name=plugin_name, error=str(e))
            )
        except Exception as e:
            QMessageBox.critical(
                self, tr("msg.plugin_error_title"),
                tr("msg.plugin_error", error=str(e))
            )

    def _on_plugin_dialog_closed(self, dialog):
        """Remove dialog from tracking list when closed."""
        if dialog in self._open_plugin_dialogs:
            self._open_plugin_dialogs.remove(dialog)

    def _reset_layout(self):
        reply = QMessageBox.question(
            self, "Resetar Layout",
            "Deseja resetar o layout dos painéis para o padrão?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.settings.clear()
            QMessageBox.information(
                self, "Layout Resetado",
                "O layout foi resetado. Reinicie o programa para aplicar as mudanças."
            )

    def _toggle_app_theme(self, checked: bool):
        """Alterna entre tema claro e escuro - estilo DAW/Pro Tools para conforto visual."""
        app = QApplication.instance()
        palette = QPalette()
        
        # Cores base para evitar fadiga ocular (sem preto/branco puros)
        if checked:
            # === TEMA ESCURO - DAW Style ===
            self._is_dark_theme = True
            self.act_toggle_theme.setText("🌙 Modo Escuro")
            
            # Paleta: cinzas suaves, sem preto puro, contraste ~85%
            palette.setColor(QPalette.ColorRole.Window, QColor(38, 38, 42))        # Fundo principal
            palette.setColor(QPalette.ColorRole.WindowText, QColor(210, 210, 215)) # Texto ~85% contraste
            palette.setColor(QPalette.ColorRole.Base, QColor(30, 30, 34))          # Áreas de entrada
            palette.setColor(QPalette.ColorRole.AlternateBase, QColor(42, 42, 46))
            palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(50, 50, 54))
            palette.setColor(QPalette.ColorRole.ToolTipText, QColor(210, 210, 215))
            palette.setColor(QPalette.ColorRole.Text, QColor(210, 210, 215))
            palette.setColor(QPalette.ColorRole.Button, QColor(50, 50, 54))
            palette.setColor(QPalette.ColorRole.ButtonText, QColor(210, 210, 215))
            palette.setColor(QPalette.ColorRole.BrightText, QColor(255, 120, 100)) # Erro (reservado)
            palette.setColor(QPalette.ColorRole.Link, QColor(130, 170, 210))       # Azul suave
            palette.setColor(QPalette.ColorRole.Highlight, QColor(70, 130, 180))   # Seleção azul sutil
            palette.setColor(QPalette.ColorRole.HighlightedText, QColor(240, 240, 245))
            palette.setColor(QPalette.ColorRole.PlaceholderText, QColor(120, 120, 125))
            
            # Stylesheet DAW Dark - moderno e suave
            dark_stylesheet = """
                * { 
                    font-family: "Segoe UI", "SF Pro Text", "Roboto", -apple-system, sans-serif;
                }
                
                QWidget { 
                    background-color: #1c1c1e; 
                    color: #ebebf0;
                }
                
                QMainWindow { 
                    background-color: #171719; 
                }
                
                QDialog { 
                    background-color: #242428; 
                    border: 1px solid #323236;
                    border-radius: 12px; 
                }
                
                /* Menu Bar - discreto */
                QMenuBar { 
                    background-color: #242428; 
                    color: #ebebf0; 
                    padding: 2px;
                    border-bottom: 1px solid #323236;
                }
                QMenuBar::item { 
                    padding: 4px 8px; 
                    border-radius: 8px; 
                    margin-right: 4px;
                }
                QMenuBar::item:selected { 
                    background-color: rgba(255, 255, 255, 0.1); 
                }
                
                QMenu { 
                    background-color: #2c2c30; 
                    color: #ebebf0; 
                    border: 1px solid #3e3e42;
                    border-radius: 6px; 
                    padding: 4px;
                }
                QMenu::item { 
                    padding: 4px 28px 4px 12px;
                    border-radius: 4px;
                    margin-bottom: 0px;
                }
                QMenu::item:selected { 
                    background-color: #0A84FF;  /* Azul vibrante moderno */
                    color: white;
                }
                QMenu::separator { 
                    height: 1px; 
                    background: #45454a; 
                    margin: 6px 12px; 
                }
                
                /* Toolbar - compacta e funcional */
                QToolBar { 
                    background-color: #242428; 
                    border: none;
                    border-bottom: 1px solid #323236;
                    spacing: 4px; 
                    padding: 2px 4px;
                }
                QToolBar::separator { 
                    width: 1px; 
                    background: #3e3e42; 
                    margin: 4px 8px; 
                }
                QToolButton { 
                    background: transparent; 
                    border: 1px solid transparent; 
                    border-radius: 6px; 
                    padding: 4px 8px; 
                    color: #ebebf0;
                }
                QToolButton:hover { 
                    background-color: rgba(255, 255, 255, 0.08); 
                    border: 1px solid rgba(255, 255, 255, 0.05);
                }
                QToolButton:pressed { 
                    background-color: rgba(0, 0, 0, 0.2); 
                    border: 1px solid transparent;
                }
                
                /* Input Fields */
                QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {
                    background-color: #171719;
                    border: 1px solid #323236;
                    border-radius: 6px;
                    padding: 4px 6px;
                    color: #ebebf0;
                    selection-background-color: #0A84FF;
                }
                QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {
                    border: 1px solid #0A84FF;
                    background-color: #1c1c1e;
                }
                
                /* Push Buttons - Modern OS Integration */
                QPushButton {
                    background-color: #343438;
                    color: #ebebf0;
                    border: 1px solid #4a4a50;
                    border-bottom: 2px solid #28282c;
                    border-radius: 6px;
                    padding: 4px 12px;
                }
                QPushButton:hover {
                    background-color: #3e3e42;
                    border: 1px solid #55555a;
                    border-bottom: 2px solid #323236;
                }
                QPushButton:pressed {
                    background-color: #28282c;
                    border: 1px solid #28282c;
                    border-bottom: 1px solid #28282c;
                    padding-top: 5px;
                    padding-bottom: 3px;
                    color: white;
                }
                
                /* Sliders */
                QSlider::groove:horizontal {
                    border: 1px solid #3a3a3e;
                    height: 6px;
                    background: #1e1e22;
                    margin: 2px 0;
                    border-radius: 3px;
                }
                QSlider::handle:horizontal {
                    background: #55555a;
                    border: 1px solid #55555a;
                    width: 14px;
                    height: 14px;
                    margin: -5px 0;
                    border-radius: 7px;
                }
                QSlider::handle:horizontal:hover {
                    background: #66666b;
                    border: 1px solid #66666b;
                }
                
                /* Scrollbars */
                QScrollBar:vertical {
                    border: none;
                    background: #2a2a2e;
                    width: 10px;
                    margin: 0px 0px 0px 0px;
                    border-radius: 5px;
                }
                QScrollBar::handle:vertical {
                    background: #45454a;
                    min-height: 20px;
                    border-radius: 5px;
                    margin: 2px;
                }
                QScrollBar::handle:vertical:hover {
                    background: #55555a;
                }
                QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                    height: 0px;
                }
                QScrollBar:horizontal {
                    border: none;
                    background: #2a2a2e;
                    height: 10px;
                    margin: 0px 0px 0px 0px;
                    border-radius: 5px;
                }
                QScrollBar::handle:horizontal {
                    background: #45454a;
                    min-width: 20px;
                    border-radius: 5px;
                    margin: 2px;
                }
                QScrollBar::handle:horizontal:hover {
                    background: #55555a;
                }
                QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
                    width: 0px;
                }
                
                /* Tabela - limpa, legível */
                QTableWidget { 
                    background-color: #1c1c1e; 
                    alternate-background-color: #202022;
                    color: #ebebf0; 
                    gridline-color: #2c2c30;
                    selection-background-color: rgba(10, 132, 255, 0.3);
                    selection-color: #ffffff;
                    border: 1px solid #323236;
                    border-radius: 8px;
                }
                QTableWidget::item { 
                    padding: 2px 4px;
                    border: none;
                }
                QTableWidget::item:selected { 
                    background-color: rgba(10, 132, 255, 0.4);
                }
                QHeaderView::section { 
                    background-color: #242428; 
                    color: #b0b0b5;
                    padding: 4px 6px;
                    border: none;
                    border-right: 1px solid #323236;
                    border-bottom: 1px solid #323236;
                    font-weight: 500;
                }
                

                
                /* GroupBox - containers */
                QGroupBox { 
                    background-color: #2a2a2e;
                    border: 1px solid #3a3a3e;
                    border-radius: 6px;
                    margin-top: 10px;
                    padding: 8px 6px 6px 6px;
                }
                QGroupBox::title { 
                    color: #a0a0a5;
                    subcontrol-origin: margin;
                    left: 12px;
                    top: 2px;
                    padding: 0 6px;
                    font-weight: 500;
                }
                
                /* Checkboxes */
                QCheckBox, QRadioButton { 
                    color: #d2d2d7;
                    spacing: 8px;
                }
                QCheckBox::indicator { 
                    width: 16px;
                    height: 16px;
                    border-radius: 3px;
                    background-color: #1e1e22;
                    border: 1px solid #4a4a4e;
                }
                QCheckBox::indicator:checked { 
                    background-color: #4682b4;
                    border: none;
                }
                QRadioButton::indicator { 
                    width: 16px;
                    height: 16px;
                    border-radius: 8px;
                    background-color: #1e1e22;
                    border: 1px solid #4a4a4e;
                }
                QRadioButton::indicator:checked { 
                    background-color: #4682b4;
                    border: none;
                }
                
                /* Sliders */
                QSlider::groove:horizontal { 
                    background: #3a3a3e;
                    height: 4px;
                    border-radius: 2px;
                }
                QSlider::handle:horizontal { 
                    background: #6a6a6e;
                    width: 14px;
                    height: 14px;
                    margin: -5px 0;
                    border-radius: 7px;
                }
                QSlider::handle:horizontal:hover {
                    background: #7a7a7e;
                }
                QSlider::sub-page:horizontal { 
                    background: #4682b4;
                    border-radius: 2px;
                }
                
                /* StatusBar */
                QStatusBar { 
                    background-color: #2a2a2e;
                    color: #909095;
                    border-top: 1px solid #3a3a3e;
                    padding: 2px 6px;
                }
                
                /* DockWidget */
                QDockWidget { 
                    color: #d2d2d7;
                    titlebar-close-icon: url(none);
                }
                QDockWidget::title { 
                    background-color: #2e2e32;
                    padding: 4px 6px;
                    border-bottom: 1px solid #3a3a3e;
                }
                
                /* Labels */
                QLabel { 
                    color: #d2d2d7;
                    background: transparent;
                }
                
                /* Splitter handles - para redimensionamento */
                QSplitter::handle {
                    background-color: #3a3a3e;
                }
                QSplitter::handle:horizontal {
                    width: 3px;
                }
                QSplitter::handle:vertical {
                    height: 3px;
                }
                QSplitter::handle:hover {
                    background-color: #4682b4;
                }
                
                /* Tabs */
                QTabWidget::pane { 
                    background-color: #26262a;
                    border: 1px solid #3a3a3e;
                    border-radius: 4px;
                }
                QTabBar::tab { 
                    background-color: #2a2a2e;
                    color: #909095;
                    padding: 4px 12px;
                    border: 1px solid #3a3a3e;
                    border-top: none;
                    border-bottom: 1px solid #3a3a3e; /* Added for South position */
                    border-radius: 0 0 4px 4px;
                    margin-right: 2px;
                }
                QTabBar::tab:selected { 
                    background-color: #26262a;
                    color: #d2d2d7;
                    border-bottom: 1px solid #26262a;
                    border-top: 2px solid #0A84FF; /* Added for South position */
                }
            """
            app.setStyleSheet(dark_stylesheet)
            self.statusBar().showMessage("🌙 Tema DAW Escuro ativado", 2000)
            
            # Barra de progresso sutil
            self._progress_bar.setStyleSheet("""
                QProgressBar { 
                    border: 1px solid #3a3a3e;
                    border-radius: 3px;
                    background: #1e1e22;
                    height: 6px;
                }
                QProgressBar::chunk { 
                    border-radius: 2px;
                    background: #5a9a5a;  /* Verde sutil para progresso */
                }
            """)
            self._progress_label.setStyleSheet("""
                QLabel { 
                    color: #909095;
                    font-size: 12px;
                    padding: 0 8px;
                    background: transparent;
                }
            """)
            
        else:
            # === TEMA CLARO - DAW Style ===
            self._is_dark_theme = False
            self.act_toggle_theme.setText("☀️ Modo Claro")
            
            # Paleta: cinzas claros, sem branco puro, contraste ~85%
            palette.setColor(QPalette.ColorRole.Window, QColor(235, 235, 238))     # Fundo principal
            palette.setColor(QPalette.ColorRole.WindowText, QColor(45, 45, 50))    # Texto ~85% contraste
            palette.setColor(QPalette.ColorRole.Base, QColor(250, 250, 252))       # Áreas de entrada
            palette.setColor(QPalette.ColorRole.AlternateBase, QColor(242, 242, 245))
            palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(255, 255, 250))
            palette.setColor(QPalette.ColorRole.ToolTipText, QColor(45, 45, 50))
            palette.setColor(QPalette.ColorRole.Text, QColor(45, 45, 50))
            palette.setColor(QPalette.ColorRole.Button, QColor(230, 230, 233))
            palette.setColor(QPalette.ColorRole.ButtonText, QColor(45, 45, 50))
            palette.setColor(QPalette.ColorRole.BrightText, QColor(200, 60, 50))   # Erro
            palette.setColor(QPalette.ColorRole.Link, QColor(50, 100, 150))        # Azul suave
            palette.setColor(QPalette.ColorRole.Highlight, QColor(70, 130, 180))   # Seleção
            palette.setColor(QPalette.ColorRole.HighlightedText, QColor(250, 250, 252))
            palette.setColor(QPalette.ColorRole.PlaceholderText, QColor(140, 140, 145))
            
            # Stylesheet DAW Light
            light_stylesheet = """
                * { 
                    font-family: "Segoe UI", "SF Pro Text", "Roboto", -apple-system, sans-serif;
                }
                
                QWidget { 
                    background-color: #ebebee; 
                    color: #2d2d32;
                }
                
                QMainWindow { 
                    background-color: #e0e0e4; 
                }
                
                QDialog { 
                    background-color: #f5f5f8; 
                    border: 1px solid #d0d0d4;
                    border-radius: 6px; 
                }
                
                /* Menu Bar */
                QMenuBar { 
                    background-color: #e5e5e8; 
                    color: #2d2d32; 
                    padding: 2px 4px;
                    border-bottom: 1px solid #d0d0d4;
                }
                QMenuBar::item { 
                    padding: 2px 8px; 
                    border-radius: 4px; 
                }
                QMenuBar::item:selected { 
                    background-color: #d5d5d8;
                }
                
                QMenu { 
                    background-color: #f5f5f8; 
                    color: #2d2d32; 
                    border: 1px solid #d0d0d4;
                    border-radius: 6px; 
                    padding: 4px;
                }
                QMenu::item { 
                    padding: 4px 24px 4px 12px;
                    border-radius: 4px;
                }
                QMenu::item:selected { 
                    background-color: #4682b4;
                    color: #fafafc;
                }
                QMenu::separator { 
                    height: 1px; 
                    background: #d0d0d4; 
                    margin: 4px 8px; 
                }
                
                /* Toolbar */
                QToolBar { 
                    background-color: #f5f5f8; 
                    border: none;
                    border-bottom: 1px solid #d0d0d4;
                    spacing: 4px; 
                    padding: 2px 4px;
                }
                QToolBar::separator { 
                    width: 1px; 
                    background: #d0d0d4; 
                    margin: 4px 6px; 
                }
                QToolButton { 
                    background: transparent; 
                    border: 1px solid transparent; 
                    border-radius: 6px; 
                    padding: 2px 6px; 
                    color: #2d2d32;
                }
                QToolButton:hover { 
                    background-color: rgba(0, 0, 0, 0.05); 
                    border: 1px solid rgba(0, 0, 0, 0.03);
                }
                QToolButton:pressed { 
                    background-color: rgba(0, 0, 0, 0.08); 
                    border: 1px solid transparent;
                }
                
                /* Tabela */
                QTableWidget { 
                    background-color: #ffffff; 
                    alternate-background-color: #f8f8fa;
                    color: #2d2d32; 
                    gridline-color: #ebebee;
                    selection-background-color: rgba(10, 132, 255, 0.2);
                    selection-color: #000000;
                    border: 1px solid #d0d0d4;
                    border-radius: 8px;
                }
                QTableWidget::item { 
                    padding: 2px 4px;
                    border: none;
                    color: #2d2d32;
                }
                QTableWidget::item:selected { 
                    background-color: rgba(10, 132, 255, 0.3);
                }
                QHeaderView::section { 
                    background-color: #f5f5f8; 
                    color: #606065;
                    padding: 4px 6px;
                    border: none;
                    border-right: 1px solid #d0d0d4;
                    border-bottom: 1px solid #d0d0d4;
                    font-weight: 500;
                }
                
                /* Inputs */
                QLineEdit, QSpinBox, QComboBox { 
                    background-color: #ffffff; 
                    color: #2d2d32; 
                    border: 1px solid #c0c0c4;
                    border-bottom: 2px solid #d0d0d4;
                    border-radius: 6px; 
                    padding: 4px 6px;
                    selection-background-color: #0A84FF;
                    selection-color: white;
                }
                QLineEdit:focus, QSpinBox:focus, QComboBox:focus { 
                    border: 1px solid #0A84FF;
                    border-bottom: 2px solid #0A84FF;
                }
                
                /* Botões */
                QPushButton { 
                    background-color: #ffffff; 
                    color: #2d2d32; 
                    border: 1px solid #c0c0c4;
                    border-bottom: 2px solid #d0d0d4;
                    border-radius: 6px;
                    padding: 4px 12px;
                    font-weight: 500;
                }
                QPushButton:hover { 
                    background-color: #f4f4f7;
                    border: 1px solid #b0b0b4;
                    border-bottom: 2px solid #c0c0c4;
                }
                QPushButton:pressed { 
                    background-color: #e8e8eb;
                    border: 1px solid #b0b0b4;
                    border-bottom: 1px solid #b0b0b4;
                    padding-top: 5px;
                    padding-bottom: 3px;
                }
                QPushButton:default { 
                    background-color: #0A84FF;
                    color: white;
                    border: 1px solid #005bb5;
                    border-bottom: 2px solid #004494;
                }
                
                /* ScrollBars */
                QScrollBar:vertical { 
                    background: #ebebee; 
                    width: 10px;
                    border-radius: 5px;
                }
                QScrollBar::handle:vertical { 
                    background-color: #c0c0c4;
                    border-radius: 5px;
                    min-height: 30px;
                    margin: 2px;
                }
                QScrollBar::handle:vertical:hover { 
                    background-color: #a0a0a4;
                }
                QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { 
                    height: 0;
                }
                QScrollBar:horizontal { 
                    background: #ebebee; 
                    height: 10px;
                    border-radius: 5px;
                }
                QScrollBar::handle:horizontal { 
                    background-color: #c0c0c4;
                    border-radius: 5px;
                    min-width: 30px;
                    margin: 2px;
                }
                
                /* GroupBox */
                QGroupBox { 
                    background-color: #f0f0f3;
                    border: 1px solid #d0d0d4;
                    border-radius: 6px;
                    margin-top: 10px;
                    padding: 8px 6px 6px 6px;
                }
                QGroupBox::title { 
                    color: #606065;
                    subcontrol-origin: margin;
                    left: 12px;
                    top: 2px;
                    padding: 0 6px;
                    font-weight: 500;
                }
                
                /* Checkboxes */
                QCheckBox, QRadioButton { 
                    color: #2d2d32;
                    spacing: 8px;
                }
                QCheckBox::indicator { 
                    width: 18px;
                    height: 18px;
                    border-radius: 4px;
                    background-color: #ffffff;
                    border: 1px solid #b0b0b4;
                }
                QCheckBox::indicator:checked { 
                    background-color: #0A84FF;
                    image: url(icons/check_white.png); /* Fallback to color if no check icon */
                    border: none;
                }
                QRadioButton::indicator { 
                    width: 18px;
                    height: 18px;
                    border-radius: 9px;
                    background-color: #ffffff;
                    border: 1px solid #b0b0b4;
                }
                QRadioButton::indicator:checked { 
                    background-color: #0A84FF;
                    border: 4px solid #ffffff;
                }
                
                /* Sliders */
                QSlider::groove:horizontal { 
                    background: #d0d0d4;
                    height: 6px;
                    border-radius: 3px;
                }
                QSlider::handle:horizontal { 
                    background: #ffffff;
                    border: 1px solid #a0a0a4;
                    width: 16px;
                    height: 16px;
                    margin: -5px 0;
                    border-radius: 8px;
                }
                QSlider::handle:horizontal:hover {
                    background: #f4f4f7;
                }
                QSlider::sub-page:horizontal { 
                    background: #0A84FF;
                    border-radius: 3px;
                }
                
                /* StatusBar */
                QStatusBar { 
                    background-color: #e5e5e8;
                    color: #606065;
                    border-top: 1px solid #d0d0d4;
                    padding: 2px 6px;
                }
                
                /* DockWidget */
                QDockWidget { 
                    color: #2d2d32;
                }
                QDockWidget::title { 
                    background-color: #e5e5e8;
                    padding: 4px 6px;
                    border-bottom: 1px solid #d0d0d4;
                }
                
                /* Labels */
                QLabel { 
                    color: #2d2d32;
                    background: transparent;
                }
                
                /* Splitter */
                QSplitter::handle {
                    background-color: #d0d0d4;
                }
                QSplitter::handle:horizontal {
                    width: 3px;
                }
                QSplitter::handle:vertical {
                    height: 3px;
                }
                QSplitter::handle:hover {
                    background-color: #4682b4;
                }
                
                /* Tabs */
                QTabWidget::pane { 
                    background-color: #ffffff;
                    border: 1px solid #d0d0d4;
                    border-radius: 8px;
                }
                QTabBar::tab { 
                    background-color: #f5f5f8;
                    color: #606065;
                    padding: 4px 12px;
                    border: 1px solid #d0d0d4;
                    border-top: none;
                    border-radius: 0 0 6px 6px;
                    margin-right: 2px;
                }
                QTabBar::tab:selected { 
                    background-color: #ebebee;
                    color: #2d2d32;
                    border-bottom: 1px solid #ebebee;
                }
            """
            app.setStyleSheet(light_stylesheet)
            self.statusBar().showMessage("☀️ Tema DAW Claro ativado", 2000)
            
            # Barra de progresso
            self._progress_bar.setStyleSheet("""
                QProgressBar { 
                    border: 1px solid #d0d0d4;
                    border-radius: 3px;
                    background: #fafafc;
                    height: 6px;
                }
                QProgressBar::chunk { 
                    border-radius: 2px;
                    background: #5a9a5a;
                }
            """)
            self._progress_label.setStyleSheet("""
                QLabel { 
                    color: #606065;
                    font-size: 12px;
                    padding: 0 8px;
                    background: transparent;
                }
            """)
        
        app.setPalette(palette)

    def _toggle_spectrogram(self, checked: bool):
        """Ativa/desativa a visualização do espectrograma."""
        self.waveform.set_show_spectrogram(checked)
        if checked:
            self.statusBar().showMessage("Espectrograma ativado", 2000)
        else:
            self.statusBar().showMessage("Espectrograma desativado", 2000)

    def _toggle_spectrogram_config_dock(self):
        """Abre ou fecha o dock de configuração do espectrograma."""
        if self._dock_spectrogram_config.isVisible():
            self._dock_spectrogram_config.hide()
        else:
            self._dock_spectrogram_config.show()

    def _toggle_preset_dock(self):
        """Abre ou fecha o dock de configuração de presets."""
        if self._preset_config_dock.isVisible():
            self._preset_config_dock.hide()
        else:
            self._preset_config_dock.show()

    def toggle_minimap(self, checked: bool):
        """Ativa/desativa o minimapa."""
        self.waveform.set_show_minimap(checked)
        if checked:
            self.statusBar().showMessage("Minimapa ativado", 2000)
        else:
            self.statusBar().showMessage("Minimapa desativado", 2000)
    
    def _toggle_srp(self, checked: bool):
        self._srp_enabled = checked
        self.waveform.set_srp_enabled(checked)
        
        # SRP e SRnA são mutuamente exclusivos
        if checked and self.act_toggle_srna.isChecked():
            self.act_toggle_srna.setChecked(False)
            self._srna_enabled = False
            self.waveform.set_srna_enabled(False)
        
        if checked:
            self.statusBar().showMessage("SRP ativado - mover preutterance move offset/cutoff", 2000)
        else:
            self.statusBar().showMessage("SRP desativado", 2000)

    def _toggle_srna(self, checked: bool):
        """Ativa/desativa SRnA (Snap Relativo a Nada)."""
        self._srna_enabled = checked
        self.waveform.set_srna_enabled(checked)
        
        # SRnA e SRP são mutuamente exclusivos
        if checked and self.act_toggle_srp.isChecked():
            self.act_toggle_srp.setChecked(False)
            self._srp_enabled = False
            self.waveform.set_srp_enabled(False)
        
        if checked:
            self.statusBar().showMessage("SRnA ativado - parâmetros movem independentemente", 2000)
        else:
            self.statusBar().showMessage("SRnA desativado", 2000)

    def _toggle_persistent_zoom(self, checked: bool):
        self._keep_zoom_on_alias_changes = checked
        self.waveform.set_persistent_zoom(checked)
        if checked:
            self.statusBar().showMessage("Zoom persistente ativado", 2000)
        else:
            self.statusBar().showMessage("Zoom persistente desativado", 2000)

    def _change_language(self, lang_code: str) -> None:
        """Troca o idioma da interface."""
        from core.translator import get_translator
        translator = get_translator()
        
        if translator.load_language(lang_code):
            # Atualiza checkbox do menu
            if hasattr(self, '_language_actions') and lang_code in self._language_actions:
                self._language_actions[lang_code].setChecked(True)
            
            # Atualiza interface com novas traduções
            self._retranslate_ui()
            
            lang_name = translator.tr("language.name")
            self.statusBar().showMessage(f"Idioma alterado para {lang_name}", 3000)
            
            # Salva imediatamente
            self.settings.setValue("language", lang_code)
        else:
            self.statusBar().showMessage(f"Erro ao carregar idioma: {lang_code}", 3000)

    def _retranslate_ui(self) -> None:
        """Atualiza todos os textos da interface com as traduções atuais."""
        from core.translator import tr
        
        # === TÍTULOS DOS MENUS PRINCIPAIS ===
        self.m_file.setTitle(tr("menu.file"))
        self.m_edit.setTitle(tr("menu.edit"))
        self.m_view.setTitle(tr("menu.view"))
        self.m_playback.setTitle(tr("menu.playback"))
        self.m_settings.setTitle(tr("menu.settings"))
        self.m_encoding.setTitle(tr("menu.encoding"))
        self.m_render.setTitle(tr("menu.render"))
        self.m_plugins.setTitle(tr("menu.plugins"))
        self.m_language.setTitle(tr("menu.language"))
        
        # === TÍTULOS DOS SUBMENUS ===
        self.m_recent_projects.setTitle(tr("file.recent_projects"))
        self.m_presets.setTitle(tr("edit.apply_preset"))
        self.m_snap_mode.setTitle(tr("edit.snap_mode"))
        self.m_gpu.setTitle(tr("menu.gpu"))
        self.m_enc_openutau.setTitle("OpenUTAU")
        self.m_enc_utau.setTitle(tr("menu.enc_utau"))
        self.m_enc_outros.setTitle(tr("menu.enc_other"))
        self.m_plugins_auto.setTitle(tr("menu.plugins_auto"))
        self.m_plugins_analysis.setTitle(tr("menu.plugins_analysis"))
        self.m_plugins_manage.setTitle(tr("menu.plugins_manage"))
        self.m_plugins_convert.setTitle(tr("menu.plugins_convert"))
        self.m_plugins_validate.setTitle(tr("menu.plugins_validate"))
        
        # === AÇÕES DO MENU ARQUIVO ===
        self.act_open_voicebank.setText(tr("file.open_voicebank"))
        self.act_open_oto.setText(tr("file.open_oto"))
        self.act_open_project.setText(tr("file.open_project"))
        self.act_save_project.setText(tr("file.save_project"))
        self.act_reveal_voicebank.setText(tr("file.reveal"))
        self.act_save.setText(tr("file.save"))
        self.act_save_as.setText(tr("file.save_as"))
        self.act_reload.setText(tr("file.reload"))
        self.act_quit.setText(tr("file.exit"))
        
        # === AÇÕES DO MENU EDITAR ===
        self.act_undo.setText(tr("edit.undo"))
        self.act_redo.setText(tr("edit.redo"))
        self.act_batch_edit.setText(tr("edit.batch_edit"))
        self.act_snap.setText(tr("edit.snap"))
        self.act_snap_peaks.setText(tr("edit.snap_peaks"))
        self.act_snap_zero_crossing.setText(tr("edit.snap_zero"))
        self.act_snap_none.setText(tr("edit.snap_none"))
        self.act_rename_alias.setText(tr("edit.rename"))
        self.act_delete_alias.setText(tr("edit.delete"))
        self.act_duplicate_alias.setText(tr("edit.duplicate"))
        self.act_mark_complete.setText(tr("edit.mark_complete"))
        self.act_keybinding_config.setText(tr("edit.keybindings"))
        
        # === AÇÕES DO MENU VISUALIZAÇÃO ===
        self.act_show_minimap.setText(tr("view.minimap"))
        self.act_show_spectrogram.setText(tr("view.spectrogram"))
        self.act_spectrogram_config.setText(tr("view.spectrogram_config"))
        # GPU agora é automática - não precisa de tradução
        self.act_normalize_waveform.setText(tr("view.normalize"))
        self.act_persistent_zoom.setText(tr("view.persistent_zoom"))
        self.act_reset_layout.setText(tr("view.reset_layout"))
        self.act_zoom_in.setText(tr("view.zoom_in"))
        self.act_zoom_out.setText(tr("view.zoom_out"))
        self.act_zoom_reset.setText(tr("view.zoom_reset"))
        self.act_cycle_wave_theme.setText(tr("view.cycle_theme"))
        
        # === AÇÕES DO MENU REPRODUÇÃO ===
        self.act_play_segment.setText(tr("playback.segment"))
        self.act_play_full.setText(tr("playback.full"))
        self.act_sector_playback.setText(tr("playback.sector"))
        self.act_audio_device.setText(tr("playback.device"))
        self.act_toggle_auto_save.setText(tr("playback.auto_save"))
        
        # === MODOS DE EDIÇÃO ===
        self.act_toggle_srp.setText(tr("mode.srp"))
        self.act_toggle_srna.setText(tr("mode.srna"))
        
        # === PRESETS ===
        self.act_show_preset_config.setText(tr("preset.config"))
        
        # === TEMAS DE ONDA ===
        self.act_wave_blue.setText(tr("theme.blue"))
        self.act_wave_green.setText(tr("theme.green"))
        self.act_wave_mono.setText(tr("theme.mono"))
        self.act_wave_orange.setText(tr("theme.orange"))
        self.act_wave_purple.setText(tr("theme.purple"))
        self.act_wave_cyan.setText(tr("theme.cyan"))
        self.act_wave_pink.setText(tr("theme.pink"))
        self.act_wave_gold.setText(tr("theme.gold"))
        self.act_wave_red.setText(tr("theme.red"))
        
        # === PLUGINS ===
        self.act_manage_plugins.setText(tr("plugin.manage"))
        self.act_plugin_vv_detector.setText(tr("plugin.vv_detector"))
        self.act_plugin_pitch.setText(tr("plugin.pitch"))
        self.act_plugin_mic_tuner.setText(tr("plugin.mic_tuner"))
        self.act_plugin_rename.setText(tr("plugin.rename"))
        self.act_plugin_sort.setText(tr("plugin.sort"))
        self.act_plugin_romaji.setText(tr("plugin.romaji"))
        self.act_plugin_duplicates.setText(tr("plugin.duplicates"))
        self.act_plugin_consistency.setText(tr("plugin.consistency"))
        
        # === ENCODINGS ===
        self.act_encoding_auto.setText(tr("encoding.auto"))
        self.act_encoding_utf8.setText(tr("encoding.utf8"))
        self.act_encoding_utf8_bom.setText(tr("encoding.utf8_bom"))
        self.act_encoding_cp932.setText(tr("encoding.cp932"))
        self.act_encoding_eucjp.setText(tr("encoding.eucjp"))
        self.act_encoding_ansi.setText(tr("encoding.ansi"))
        self.act_encoding_latin1.setText(tr("encoding.latin1"))
        self.act_encoding_gbk.setText(tr("encoding.gbk"))
        self.act_encoding_euckr.setText(tr("encoding.euckr"))
        
        # === COLUNAS DA TABELA ===
        headers = [
            tr("column.favorite"),
            tr("column.filename"),
            tr("column.alias"),
            tr("column.offset"),
            tr("column.overlap"),
            tr("column.preutter"),
            tr("column.consonant"),
            tr("column.cutoff")
        ]
        self.table.setHorizontalHeaderLabels(headers)

    def _toggle_normalize_waveform(self, checked: bool):
        self.waveform.set_normalize_enabled(checked)
        if checked:
            self.statusBar().showMessage("Normalização ativada - use ALT+Scroll para zoom vertical", 2000)
        else:
            self.statusBar().showMessage("Normalização desativada - amplitude real", 2000)
        # Recarrega a waveform atual para aplicar a mudança
        self._load_waveform_for_current_row()

    def _toggle_sector_playback(self, checked: bool):
        self.waveform.set_sector_playback_enabled(checked)
        if checked:
            self.statusBar().showMessage("Modo setor ativado - clique na waveform para ouvir setores", 2000)
        else:
            self.statusBar().showMessage("Modo setor desativado - clique toca o segmento completo", 2000)

    def _show_keybinding_config(self):
        """Abre o diálogo de configuração de teclas de parâmetros."""
        current_bindings = self.waveform.get_marker_keys()
        dialog = KeybindingConfigDialog(current_bindings, self)
        
        def apply_keybindings(bindings):
            self.waveform.set_marker_keys(bindings)
            self.statusBar().showMessage("Teclas de parâmetros atualizadas!", 2000)
        
        dialog.keybindingsChanged.connect(apply_keybindings)
        dialog.exec()

    # Dicionário de temas de waveform
    WAVE_THEMES = {
        # Temas clássicos (cores vibrantes)
        "blue": ("#0077ff", "Azul suave"),
        "green": ("#2dff88", "Verde digital"),
        "mono": ("#ffffff", "Branco sobre preto"),
        "orange": ("#ff9500", "Laranja amber"),
        "purple": ("#a855f7", "Roxo synthwave"),
        "cyan": ("#00d4ff", "Ciano terminal"),
        "pink": ("#ff1493", "Rosa neon"),
        "gold": ("#ffd700", "Dourado clássico"),
        "red": ("#ff3b30", "Vermelho intenso"),
        # Temas escuros (melhor contraste)
        "dark_teal": ("#2a9d8f", "Teal escuro"),
        "dark_slate": ("#64748b", "Slate suave"),
        "midnight": ("#4f6d7a", "Azul meia-noite"),
        "dark_navy": ("#5c7090", "Navy elegante"),
        "forest": ("#4a7c59", "Verde floresta"),
        "ocean": ("#3b6978", "Oceano profundo"),
        "sunset": ("#c97b5d", "Pôr do sol"),
        "lavender": ("#9b8aa8", "Lavanda suave"),
    }
    
    WAVE_THEME_ORDER = [
        "blue", "green", "mono", "orange", "purple", "cyan", "pink", "gold", "red",
        "dark_teal", "dark_slate", "midnight", "dark_navy", "forest", "ocean", "sunset", "lavender"
    ]

    def _set_wave_theme(self, theme_key: str):
        """Define o tema de waveform pelo nome"""
        if theme_key in self.WAVE_THEMES:
            color, name = self.WAVE_THEMES[theme_key]
            self.waveform.set_wave_colors(color)
            self.statusBar().showMessage(f"Tema: {name}", 1500)

    def _cycle_wave_theme(self):
        """Cicla entre todos os temas disponíveis"""
        # Encontra qual tema está ativo
        theme_actions = {
            "blue": self.act_wave_blue,
            "green": self.act_wave_green,
            "mono": self.act_wave_mono,
            "orange": self.act_wave_orange,
            "purple": self.act_wave_purple,
            "cyan": self.act_wave_cyan,
            "pink": self.act_wave_pink,
            "gold": self.act_wave_gold,
            "red": self.act_wave_red,
            # Temas escuros
            "dark_teal": self.act_wave_dark_teal,
            "dark_slate": self.act_wave_dark_slate,
            "midnight": self.act_wave_midnight,
            "dark_navy": self.act_wave_dark_navy,
            "forest": self.act_wave_forest,
            "ocean": self.act_wave_ocean,
            "sunset": self.act_wave_sunset,
            "lavender": self.act_wave_lavender,
        }
        
        current_idx = 0
        for i, key in enumerate(self.WAVE_THEME_ORDER):
            if theme_actions[key].isChecked():
                current_idx = i
                break
        
        # Próximo tema
        next_idx = (current_idx + 1) % len(self.WAVE_THEME_ORDER)
        next_key = self.WAVE_THEME_ORDER[next_idx]
        
        # Ativa o próximo tema
        theme_actions[next_key].setChecked(True)
        self._set_wave_theme(next_key)

    def _update_title(self):
        title = "Copaiba Lexikon | 2026.4 v120.1"
        if self._current_path:
            title += f" - {self._current_path.name}"
            if self._dirty:
                title += " *"
        self.setWindowTitle(title)

    def _restore_notes_to_table(self):
        """Restaura as anotações nas células da tabela."""
        self.table.blockSignals(True)
        for row in range(self.table.rowCount()):
            filename_item = self.table.item(row, self.COL_FILENAME)
            alias_item = self.table.item(row, self.COL_ALIAS)
            if filename_item and alias_item:
                key = f"{filename_item.text()}|{alias_item.text()}"
                if key in self._notes_data:
                    note = self._notes_data[key]
                    self.table.setItem(row, self.COL_NOTES, QTableWidgetItem(note))
        self.table.blockSignals(False)

    def keyPressEvent(self, event):
        # Enter para editar
        if event.key() == Qt.Key_Return or event.key() == Qt.Key_Enter:
            if self.table.hasFocus():
                self.table.edit(self.table.currentIndex())
                event.accept()
                return
        
        # Tab para navegar entre Aliases
        if event.key() == Qt.Key_Tab:
            if self.table.hasFocus() and self.table.currentColumn() == self.COL_ALIAS:
                next_row = self.table.currentRow() + 1
                if next_row < self.table.rowCount():
                    self.table.setCurrentCell(next_row, self.COL_ALIAS)
                    self.table.edit(self.table.model().index(next_row, self.COL_ALIAS))
                    event.accept()
                    return

        # Atalhos de waveform (Q, W, E, R, T) são tratados globalmente ou via event filter
        super().keyPressEvent(event)

    def on_zoom_slider_changed(self, value):
        """Manipula o slider de zoom."""
        if not self.waveform or not hasattr(self.waveform, '_audio_dur') or self.waveform._audio_dur <= 0:
            return
            
        # 0 = Zoom Out (ver tudo) -> width = duration
        # 100 = Zoom In (ver detalhes) -> width = min_width
        
        duration = self.waveform._audio_dur
        min_width = 0.05  # 50ms
        max_width = duration
        
        # Logarithmic mapping
        # t = value / 100.0
        # width = max_width * (min_width / max_width) ^ t
        t = value / 100.0
        try:
            width = max_width * math.pow((min_width / max_width), t)
        except:
            width = max_width

        # Get current center
        vb = self.waveform._plot.getViewBox()
        if vb is None: return
        
        current_range = vb.viewRange()[0]
        center = (current_range[0] + current_range[1]) / 2
        
        new_min = center - width / 2
        new_max = center + width / 2
        
        # Clamp
        if new_min < 0:
            new_min = 0
            new_max = width
        if new_max > duration:
            new_max = duration
            new_min = duration - width
            if new_min < 0: new_min = 0
            
        self.waveform._plot.setXRange(new_min, new_max, padding=0)

    def _cycle_wave_theme(self):
        """Cicla entre todos os temas disponíveis"""
        
        # Ordem dos temas
        WAVE_THEME_ORDER = [
            "blue", "green", "mono", "orange", "purple", "cyan", "pink", "gold", "red",
            "dark_teal", "dark_slate", "midnight", "dark_navy", "forest", "ocean", "sunset", "lavender"
        ]
        
        # Mapeia nome do tema para QAction (para setChecked)
        # As actions estão em self (foram criadas pelo menu_builder e anexadas a self)
        theme_map = {
            "blue": self.act_wave_blue, "green": self.act_wave_green,
            "mono": self.act_wave_mono, "orange": self.act_wave_orange,
            "purple": self.act_wave_purple, "cyan": self.act_wave_cyan,
            "pink": self.act_wave_pink, "gold": self.act_wave_gold,
            "red": self.act_wave_red,
            "dark_teal": self.act_wave_dark_teal, "dark_slate": self.act_wave_dark_slate,
            "midnight": self.act_wave_midnight, "dark_navy": self.act_wave_dark_navy,
            "forest": self.act_wave_forest, "ocean": self.act_wave_ocean,
            "sunset": self.act_wave_sunset, "lavender": self.act_wave_lavender,
        }

        # Descobre atual
        current_theme = self.settings.value("wave_color", "blue")
        
        # Descobre index
        try:
            current_idx = WAVE_THEME_ORDER.index(current_theme)
        except ValueError:
            current_idx = 0
            
        # Próximo
        next_idx = (current_idx + 1) % len(WAVE_THEME_ORDER)
        next_theme = WAVE_THEME_ORDER[next_idx]
        
        # Aplica
        if next_theme in theme_map:
            theme_map[next_theme].setChecked(True)
            self._set_wave_theme(next_theme)

    def _update_progress(self, specific_row: int = None):
        total = self.table.rowCount()
        completed = len(self._completed_aliases)
        
        # Atualiza a barra de progresso visual
        if total > 0:
            percentage = int((completed / total) * 100)
            self._progress_bar.setValue(percentage)
            
            # Emoji dinâmico baseado no progresso
            if percentage == 0:
                emoji = "🌱"  # Semente - começando
            elif percentage < 25:
                emoji = "🌿"  # Broto - início
            elif percentage < 50:
                emoji = "🪴"  # Planta pequena
            elif percentage < 75:
                emoji = "🌳"  # Árvore crescendo
            elif percentage < 100:
                emoji = "🌲"  # Árvore grande
            else:
                emoji = "🎉"  # Celebração - completo!
            
            self._progress_emoji.setText(emoji)
            self._progress_label.setText(f"{completed}/{total} ({percentage}%)")
        else:
            self._progress_bar.setValue(0)
            self._progress_emoji.setText("🌱")
            self._progress_label.setText("0/0 (0%)")
        
        # Atualiza cores das linhas da tabela
        self._update_row_colors(specific_row=specific_row)
        
        # Atualiza progresso no Discord Rich Presence
        if DISCORD_RPC_AVAILABLE:
            try:
                rpc = get_discord_rpc()
                rpc.set_progress(completed, total)
            except:
                pass

    def _update_row_colors(self, specific_row: int = None):
        """
        Atualiza as cores de fundo das linhas da tabela baseado no status:
        - Verde (transparente): Concluído (marcado com ✓)
        - Azul: Valores numéricos faltando ou zerados quando não deveria
        - Cinza: Não configurado (sem marca de concluído e sem problemas)
        """
        self._updating_from_code = True
        try:
            # Cores com transparência suave para não machucar os olhos
            COLOR_COMPLETED = QColor(76, 175, 80, 40)      # Verde suave transparente
            COLOR_MISSING = QColor(33, 150, 243, 50)       # Azul suave transparente
            COLOR_UNCONFIGURED = QColor(128, 128, 128, 30) # Cinza suave transparente
            COLOR_CONFLICT = QColor(255, 80, 80, 70)       # Vermelho suave de alerta
            COLOR_NONE = QColor(0, 0, 0, 0)                # Transparente (sem cor)
            
            rows_to_update = [specific_row] if specific_row is not None else range(self.table.rowCount())
            
            for row in rows_to_update:
                if row < 0 or row >= self.table.rowCount():
                    continue
                    
                # Determina o status da linha
                fav_item = self.table.item(row, self.COL_FAV)
                is_completed = fav_item and fav_item.checkState() == Qt.CheckState.Checked
                
                # Verifica se há valores faltando
                has_missing_values = False
                
                # Obtém os valores dos parâmetros
                offset_item = self.table.item(row, self.COL_OFFSET)
                overlap_item = self.table.item(row, self.COL_OVERLAP)
                preutter_item = self.table.item(row, self.COL_PREUTTER)
                consonant_item = self.table.item(row, self.COL_CONSONANT)
                cutoff_item = self.table.item(row, self.COL_CUTOFF)
                alias_item = self.table.item(row, self.COL_ALIAS)
                
                # Alias vazio é um problema
                if alias_item and (not alias_item.text() or alias_item.text().strip() == ""):
                    has_missing_values = True
                
                # Preutter e Consonant zerados geralmente indicam falta de configuração
                try:
                    preutter_val = int(preutter_item.text()) if preutter_item else 0
                    consonant_val = int(consonant_item.text()) if consonant_item else 0
                    
                    # Se ambos preutter e consonant são 0, pode indicar não configurado
                    if preutter_val == 0 and consonant_val == 0:
                        has_missing_values = True
                except (ValueError, AttributeError):
                    has_missing_values = True
                
                # Verifica conflitos de parâmetros que quebram loop em resamplers e limites
                has_conflict = False
                tooltip_msg = ""
                try:
                    def safe_float(item):
                        if not item or not item.text().strip(): return 0.0
                        return float(item.text())

                    off_val = safe_float(offset_item)
                    ovl_val = safe_float(overlap_item)
                    pre_val = safe_float(preutter_item)
                    c_val = safe_float(consonant_item)
                    cut_val = safe_float(cutoff_item)
                    
                    # 1. Falta espaço para loop (Consoante invade o limite direito relativo)
                    if cut_val < 0 and c_val >= -cut_val:
                        has_conflict = True
                        tooltip_msg = "⚠️ Conflito: O valor da Consoante ultrapassa o limite direito (Cutoff), impossibilitando o loop."
                    # 2. Overlap invadindo preutterance
                    elif pre_val != 0 and ovl_val > pre_val:
                        has_conflict = True
                        tooltip_msg = "⚠️ Erro: O Overlap (verde) não pode ser maior que o Preutterance (vermelho)."
                    # 3. Preutterance invadindo consoante
                    elif c_val != 0 and pre_val > c_val:
                        has_conflict = True
                        tooltip_msg = "⚠️ Erro: O Preutterance não deveria ser maior que a área Consonant fixa."
                    # 4. Limite Direito invadindo o Esquerdo (offset) se houver áudio ou for óbvio
                    elif hasattr(self, '_audio_dur') and self._audio_dur and cut_val > 0:
                        right_limit_ms = (self._audio_dur * 1000.0) - cut_val
                        if right_limit_ms <= off_val:
                            has_conflict = True
                            tooltip_msg = "⚠️ Erro Fatal: O Cutoff invadiu o Offset (Áudio invisível/negativo)."
                except (ValueError, AttributeError):
                    pass
                
                # Determina a cor a aplicar e seta o Tooltip
                if alias_item:
                    alias_item.setToolTip("") # Limpa tooltip
                    
                if has_conflict:
                    row_color = COLOR_CONFLICT
                    if alias_item:
                        alias_item.setToolTip(tooltip_msg)
                elif is_completed:
                    row_color = COLOR_COMPLETED
                elif has_missing_values:
                    row_color = COLOR_MISSING
                else:
                    row_color = COLOR_UNCONFIGURED
                
                # Aplica a cor a todas as células da linha
                for col in range(self.table.columnCount()):
                    item = self.table.item(row, col)
                    if item:
                        item.setBackground(row_color)
        finally:
            self._updating_from_code = False

    def _load_settings(self):
        geometry = self.settings.value("geometry")
        if geometry:
            self.restoreGeometry(geometry)

        state = self.settings.value("windowState")
        if state:
            self.restoreState(state)

        # Garante que docks essenciais estejam sempre visíveis e na posição correta
        # (o estado salvo pode tê-los fechado/movido de sessão anterior)
        if hasattr(self, '_dock_params'):
            if not self._dock_params.isVisible():
                self.addDockWidget(Qt.TopDockWidgetArea, self._dock_params)
            self._dock_params.show()
        if hasattr(self, '_dock_wave'):
            if not self._dock_wave.isVisible():
                self.addDockWidget(Qt.BottomDockWidgetArea, self._dock_wave)
            self._dock_wave.show()

        encoding = self.settings.value("encoding", "auto")
        self._set_encoding(encoding)

        snap_enabled = self.settings.value("snap_enabled", False, type=bool)
        self.act_snap.setChecked(snap_enabled)
        self.waveform.set_snap_enabled(snap_enabled)

        snap_mode = self.settings.value("snap_mode", "peaks")
        if snap_mode == "peaks":
            self.act_snap_peaks.setChecked(True)
        elif snap_mode == "zero_crossing":
            self.act_snap_zero_crossing.setChecked(True)
        else:
            self.act_snap_none.setChecked(True)
        self.waveform.set_snap_mode(snap_mode)

        wave_color_scheme = self.settings.value("wave_color", "blue")
        theme_actions = {
            "blue": self.act_wave_blue, "green": self.act_wave_green,
            "mono": self.act_wave_mono, "orange": self.act_wave_orange,
            "purple": self.act_wave_purple, "cyan": self.act_wave_cyan,
            "pink": self.act_wave_pink, "gold": self.act_wave_gold,
            "red": self.act_wave_red,
            "dark_teal": self.act_wave_dark_teal, "dark_slate": self.act_wave_dark_slate,
            "midnight": self.act_wave_midnight, "dark_navy": self.act_wave_dark_navy,
            "forest": self.act_wave_forest, "ocean": self.act_wave_ocean,
            "sunset": self.act_wave_sunset, "lavender": self.act_wave_lavender,
        }
        
        # Garante que um tema válido está selecionado
        if wave_color_scheme not in theme_actions:
            wave_color_scheme = "blue"
            
        if wave_color_scheme in theme_actions:
            theme_actions[wave_color_scheme].setChecked(True)
            self._set_wave_theme(wave_color_scheme)

    def _set_wave_theme(self, theme_name: str):
        """Define o tema de cores da waveform."""
        themes = {
            "blue": "#00aaff", "green": "#00ff00", "mono": "#ffffff",
            "orange": "#ffaa00", "purple": "#aa00ff", "cyan": "#00ffff",
            "pink": "#ff00aa", "gold": "#ffd700", "red": "#ff0000",
            # Temas escuros
            "dark_teal": "#008080", "dark_slate": "#2f4f4f",
            "midnight": "#191970", "dark_navy": "#000080",
            "forest": "#228b22", "ocean": "#00ced1",
            "sunset": "#ff4500", "lavender": "#e6e6fa"
        }
        
        color = themes.get(theme_name, "#00aaff") # Azul padrão
        self.waveform.set_wave_colors(color)
        self.settings.setValue("wave_color", theme_name)
        
        # Atualiza a seleção no menu se chamado via código (ex: settings dialog)
        if hasattr(self, 'wave_color_group'):
            # Encontrar action pelo nome é chato, mas se tivermos o mapa...
            # O mapa está em load_settings, não aqui como atributo.
            # Mas podemos reconstruir ou ignorar se o group já cuida disso via signal?
            # O signal do menu chama este método. Se chamarmos este método de fora, o menu não atualiza sozinho.
            # Vamos iterar as actions do grupo.
            pass


        # --- ALTERAÇÃO: Padrão False para Minimap e Zoom Persistente ---
        show_minimap = self.settings.value("show_minimap", False, type=bool)
        self.act_show_minimap.setChecked(show_minimap)
        self.waveform.set_show_minimap(show_minimap)

        show_spectrogram = self.settings.value("show_spectrogram", False, type=bool)
        self.act_show_spectrogram.setChecked(show_spectrogram)
        self.waveform.set_show_spectrogram(show_spectrogram)

        auto_save = self.settings.value("auto_save", False, type=bool)
        self.act_toggle_auto_save.setChecked(auto_save)
        if auto_save:
            # Restaura auto-save silenciosamente sem mostrar diálogo
            saved_interval = self.settings.value("auto_save_interval", 300, type=int)
            self._restore_auto_save_silently(saved_interval)

        srp_enabled = self.settings.value("srp_enabled", False, type=bool)
        self.act_toggle_srp.setChecked(srp_enabled)
        self._toggle_srp(srp_enabled)

        srna_enabled = self.settings.value("srna_enabled", False, type=bool)
        self.act_toggle_srna.setChecked(srna_enabled)
        if srna_enabled:
            self._toggle_srna(srna_enabled)

        persistent_zoom = self.settings.value("persistent_zoom", False, type=bool)
        self.act_persistent_zoom.setChecked(persistent_zoom)
        self._toggle_persistent_zoom(persistent_zoom)

        # Tema claro/escuro
        dark_theme = self.settings.value("dark_theme", True, type=bool)
        self.act_toggle_theme.setChecked(dark_theme)
        # Aplica o tema independentemente de qual seja para forçar as modernizações da stylesheet
        self._toggle_app_theme(dark_theme)
        # Teclas de parâmetros personalizadas
        keybindings_json = self.settings.value("keybindings", "")
        if keybindings_json:
            try:
                import json
                from PySide6.QtCore import Qt
                keybindings_data = json.loads(keybindings_json)
                # Converte strings de volta para Qt.Key
                keybindings = {}
                for param, key_value in keybindings_data.items():
                    keybindings[param] = key_value
                self.waveform.set_marker_keys(keybindings)
            except (json.JSONDecodeError, Exception):
                pass  # Usa keybindings padrão se houver erro

        # GPU é ativada automaticamente nos bastidores quando disponível
        if GPU_BACKEND_AVAILABLE:
            def _delayed_gpu_init():
                self._enable_gpu_silently()
            QTimer.singleShot(100, _delayed_gpu_init)  # Ativa GPU após 100ms

        # Idioma salvo
        saved_language = self.settings.value("language", "pt_BR")
        from core.translator import get_translator
        translator = get_translator()
        if translator.get_current_language() != saved_language:
            translator.load_language(saved_language)
            # Atualiza checkbox do menu
            if hasattr(self, '_language_actions') and saved_language in self._language_actions:
                self._language_actions[saved_language].setChecked(True)

    def _save_settings(self):
        self.settings.setValue("geometry", self.saveGeometry())
        self.settings.setValue("windowState", self.saveState())
        self.settings.setValue("encoding", self._encoding)
        self.settings.setValue("snap_enabled", self.act_snap.isChecked())
        self.settings.setValue("snap_mode", self.waveform.get_snap_mode())

        # Salva tema de waveform ativo
        theme_actions = {
            "blue": self.act_wave_blue, "green": self.act_wave_green,
            "mono": self.act_wave_mono, "orange": self.act_wave_orange,
            "purple": self.act_wave_purple, "cyan": self.act_wave_cyan,
            "pink": self.act_wave_pink, "gold": self.act_wave_gold,
            "red": self.act_wave_red,
        }
        wave_color = "blue"  # padrão
        for key, action in theme_actions.items():
            if action.isChecked():
                wave_color = key
                break
        self.settings.setValue("wave_color", wave_color)

        self.settings.setValue("show_minimap", self.act_show_minimap.isChecked())
        self.settings.setValue("show_spectrogram", self.act_show_spectrogram.isChecked())
        self.settings.setValue("auto_save", self.act_toggle_auto_save.isChecked())
        self.settings.setValue("auto_save_interval", self._auto_save_interval)
        self.settings.setValue("srp_enabled", self.act_toggle_srp.isChecked())
        self.settings.setValue("srna_enabled", self.act_toggle_srna.isChecked())
        self.settings.setValue("persistent_zoom", self.act_persistent_zoom.isChecked())
        self.settings.setValue("dark_theme", getattr(self, '_is_dark_theme', True))

        # Salva teclas de parâmetros personalizadas
        try:
            import json
            keybindings = self.waveform.get_marker_keys()
            self.settings.setValue("keybindings", json.dumps(keybindings))
        except Exception:
            pass

        # GPU é agora automática - não precisa salvar preferência do usuário

        # Salva idioma selecionado
        from core.translator import get_translator
        self.settings.setValue("language", get_translator().get_current_language())
        
        # --- NOVO: Salva estado da última sessão ---
        if self._current_path and self._current_path.exists():
            self.settings.setValue("last_oto_path", str(self._current_path))
            current_row = self.table.currentRow()
            if current_row >= 0:
                self.settings.setValue("last_alias_row", current_row)
            if self._voicebank_dir:
                self.settings.setValue("last_voicebank_dir", str(self._voicebank_dir))

    def _restore_last_session(self):
        """
        Restaura a última sessão: carrega o último oto.ini e navega para o último alias.
        Chamado automaticamente no startup após um delay para permitir a UI carregar.
        """
        try:
            # Verifica se o usuário deseja restaurar sessão (pode ser desabilitado no futuro)
            auto_resume = self.settings.value("auto_resume_session", True, type=bool)
            if not auto_resume:
                return
            
            # Obtém o caminho do último oto.ini
            last_oto_path = self.settings.value("last_oto_path", "")
            if not last_oto_path:
                return
            
            last_path = Path(last_oto_path)
            if not last_path.exists():
                self.statusBar().showMessage("Último projeto não encontrado", 3000)
                return
            
            # Carrega o oto.ini
            self.project_ctrl.load_oto(last_path)
            
            # Aguarda um pouco e navega para o último alias
            last_row = self.settings.value("last_alias_row", 0, type=int)
            
            def _goto_last_alias():
                if last_row >= 0 and last_row < self.table.rowCount():
                    self.table.setCurrentCell(last_row, self.COL_ALIAS)
                    self.table.scrollToItem(self.table.item(last_row, self.COL_ALIAS))
                    self._load_waveform_for_current_row()
                    self.statusBar().showMessage(f"Sessão restaurada: linha {last_row + 1}", 3000)
                    
            QTimer.singleShot(200, _goto_last_alias)
            
        except Exception as e:
            self.statusBar().showMessage(f"Erro ao restaurar sessão: {e}", 5000)


    def cleanup_resources(self):
        """Limpa recursos antes de sair."""
        # Se for chamado via aboutToQuit, tenta uma limpeza rápida
        if hasattr(self, 'sessions'):
            for session_info in self.sessions:
                if hasattr(session_info, 'waveform'):
                    session_info.waveform.cleanup()
        
        # Garante que o player pare
        if hasattr(self, '_audio_player') and self._audio_player:
            try:
                self._audio_player.stop()
            except:
                pass
        
        # Desconecta Discord RPC
        if DISCORD_RPC_AVAILABLE:
            try:
                shutdown_discord_rpc()
            except:
                pass

    def closeEvent(self, event: QCloseEvent):
        for index, session in enumerate(self.sessions):
            if session.dirty:
                self.tab_widget.setCurrentIndex(index)
                
                completed = len(session.completed_aliases)
                total = session.table.rowCount()
                last_saved = self._get_last_saved_string()

                dialog = AdvancedExitDialog(
                    self,
                    session.current_path or Path("sem_arquivo.ini"),
                    session.dirty,
                    completed,
                    total,
                    last_saved
                )
                dialog.exec()
                action = dialog.get_action()

                if action == "cancel":
                    event.ignore()
                    return
                elif action == "backup":
                    if self._create_backup():
                        self.save_oto()
                    else:
                        reply = QMessageBox.question(
                            self, "Backup Falhou",
                            "Não foi possível criar backup. Salvar mesmo assim?",
                            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
                        )
                        if reply == QMessageBox.StandardButton.Yes:
                            self.save_oto()
                        else:
                            event.ignore()
                            return
                elif action == "save":
                    self.save_oto()

        # Salva configurações antes de fechar janelas/threads
        self._save_settings()
        
        # Limpa recursos e aguarda threads (com timeout de segurança)
        self.cleanup_resources()
        
        event.accept()


def main() -> Any:
    # Habilitar VSync para monitores de alta taxa de atualização
    import os
    os.environ.setdefault('QSG_RENDER_LOOP', 'basic')
    os.environ.setdefault('QT_QPA_EGLFS_FORCE_VSYNC', '1')
    
    # Define AppUserModelID para Windows - permite ícone correto na barra de tarefas
    if sys.platform == 'win32':
        import ctypes
        myappid = 'MiSCLabs.Copaiba.Lexikon.6.0'  # ID único do aplicativo
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(myappid)
    
    # Forçar formato de surface com VSync
    from PySide6.QtGui import QSurfaceFormat
    fmt = QSurfaceFormat()
    fmt.setSwapInterval(1)  # VSync: 1 = ativado, 0 = desativado
    fmt.setSwapBehavior(QSurfaceFormat.DoubleBuffer)
    QSurfaceFormat.setDefaultFormat(fmt)
    
    app = QApplication(sys.argv)
    app.setApplicationName("Copaiba Lexikon")
    app.setOrganizationName("POMAR LTS")

    app.setStyle("Fusion")
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor(53, 53, 53))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(255, 255, 255))
    palette.setColor(QPalette.ColorRole.Base, QColor(25, 25, 25))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor(53, 53, 53))
    palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(255, 255, 255))
    palette.setColor(QPalette.ColorRole.ToolTipText, QColor(255, 255, 255))
    palette.setColor(QPalette.ColorRole.Text, QColor(255, 255, 255))
    palette.setColor(QPalette.ColorRole.Button, QColor(53, 53, 53))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor(255, 255, 255))
    palette.setColor(QPalette.ColorRole.BrightText, QColor(255, 0, 0))
    palette.setColor(QPalette.ColorRole.Link, QColor(42, 130, 218))
    palette.setColor(QPalette.ColorRole.Highlight, QColor(42, 130, 218))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor(0, 0, 0))
    app.setPalette(palette)

    # Set application-wide icon for taskbar
    if getattr(sys, 'frozen', False):
        # Executando como .exe
        icon_path = Path(sys.executable).parent / 'favicon.ico'
    else:
        # Executando como script
        icon_path = Path(__file__).parent / 'favicon.ico'
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))

    # Splash Screen
    from splash_screen import CopaibaSplashScreen
    splash = CopaibaSplashScreen()
    splash.show()
    app.processEvents()
    
    # Etapas de carregamento com progresso visual
    splash.set_progress(10, "Inicializando interface...")
    splash.set_progress(30, "Carregando componentes...")
    
    window = MainWindow()
    
    splash.set_progress(70, "Configurando waveform...")
    splash.set_progress(90, "Finalizando configurações...")
    
    app.aboutToQuit.connect(window.cleanup_resources)
    
    splash.set_progress(100, "Pronto!")
    app.processEvents()
    
    # Mostra janela principal e fecha splash com fade out
    window.show()
    
    # Pequeno delay para ver o 100%, depois fade out
    QTimer.singleShot(85, lambda: splash.fade_out_and_close())
    
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
