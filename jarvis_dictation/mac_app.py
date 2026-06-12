from __future__ import annotations

import argparse
import logging
import signal
import threading
from logging.handlers import RotatingFileHandler

import objc
from AppKit import (
    NSApplication,
    NSApplicationActivationPolicyAccessory,
    NSBackingStoreBuffered,
    NSButton,
    NSColor,
    NSControlStateValueOn,
    NSFocusRingTypeNone,
    NSFont,
    NSFontWeightBold,
    NSFontWeightMedium,
    NSGlassEffectView,
    NSGlassEffectViewStyleClear,
    NSGlassEffectViewStyleRegular,
    NSImage,
    NSImageView,
    NSLineBreakByTruncatingTail,
    NSMenu,
    NSMenuItem,
    NSPopUpButton,
    NSSegmentedControl,
    NSSegmentStyleCapsule,
    NSSwitch,
    NSStatusBar,
    NSTextAlignmentLeft,
    NSTextField,
    NSTextFieldCell,
    NSVariableStatusItemLength,
    NSVisualEffectBlendingModeBehindWindow,
    NSVisualEffectMaterialPopover,
    NSVisualEffectStateActive,
    NSVisualEffectView,
    NSView,
    NSWindow,
    NSWindowStyleMaskClosable,
    NSWindowStyleMaskFullSizeContentView,
    NSWindowStyleMaskTitled,
    NSWorkspace,
)
from Foundation import NSMakeRect, NSObject, NSURL
from PyObjCTools import AppHelper

from jarvis_dictation.app import (
    DictationController,
    FloatingOverlay,
    RightCommandHotkey,
    available_input_devices,
)
from jarvis_dictation.model_manager import MODEL_LOG_PATH, ModelManager
from jarvis_dictation.model_server import get_server_info
from jarvis_dictation.models import MODEL_PRESETS, preset_for_model_name
from jarvis_dictation.permissions import request_accessibility_permission, request_macos_permissions
from jarvis_dictation.preferences import Preferences
from jarvis_dictation.shortcuts import shortcut_display_name


MODEL_OPTIONS = (
    ("nemotron", "Nemotron 3.5", "Fast multilingual 8-bit model", "~1.1 GB loaded"),
    ("default", "Parakeet 0.6B", "High-quality multilingual model", "~2.5 GB weights"),
    ("small-en", "Parakeet 110M", "Compact English-only model", "~459 MB weights"),
)
APP_LOG_PATH = MODEL_LOG_PATH.parent / "mac-app.log"
MODEL_TITLE_BY_PRESET = {preset: title for preset, title, _, _ in MODEL_OPTIONS}
MODEL_MENU_TITLE_BY_PRESET = {
    "nemotron": "Nemotron 3.5 (Recommended)",
    "default": "Parakeet 0.6B",
    "small-en": "Parakeet 110M (English)",
}


class VerticallyCenteredTextFieldCell(NSTextFieldCell):
    def drawingRectForBounds_(self, bounds):
        drawing_rect = objc.super(VerticallyCenteredTextFieldCell, self).drawingRectForBounds_(bounds)
        text_size = self.cellSizeForBounds_(bounds)
        if text_size.height >= drawing_rect.size.height:
            return drawing_rect
        return NSMakeRect(
            drawing_rect.origin.x,
            drawing_rect.origin.y + ((drawing_rect.size.height - text_size.height) / 2.0),
            drawing_rect.size.width,
            text_size.height,
        )


def _label(
    text: str,
    frame,
    size: float,
    weight: float,
    color,
    *,
    vertically_centered: bool = False,
) -> NSTextField:
    label = NSTextField.alloc().initWithFrame_(frame)
    if vertically_centered:
        label.setCell_(VerticallyCenteredTextFieldCell.alloc().initTextCell_(text))
    label.setEditable_(False)
    label.setBordered_(False)
    label.setDrawsBackground_(False)
    label.setSelectable_(False)
    label.setAlignment_(NSTextAlignmentLeft)
    label.setMaximumNumberOfLines_(1)
    label.setLineBreakMode_(NSLineBreakByTruncatingTail)
    label.setStringValue_(text)
    label.setFont_(NSFont.systemFontOfSize_weight_(size, weight))
    label.setTextColor_(color)
    return label


def _glass_surface(frame, corner_radius: float, tint_alpha: float = 0.035, clear: bool = False):
    glass = NSGlassEffectView.alloc().initWithFrame_(frame)
    glass.setStyle_(NSGlassEffectViewStyleClear if clear else NSGlassEffectViewStyleRegular)
    glass.setCornerRadius_(corner_radius)
    glass.setTintColor_(NSColor.colorWithCalibratedWhite_alpha_(1.0, tint_alpha))
    content = NSView.alloc().initWithFrame_(glass.bounds())
    glass.setContentView_(content)
    return glass, content


def _glass_button(root, frame, title: str, symbol: str, target, action: str) -> NSButton:
    glass, content = _glass_surface(frame, 12, tint_alpha=0.025, clear=True)
    root.addSubview_(glass)

    icon = NSImageView.alloc().initWithFrame_(NSMakeRect(14, 9, 16, 16))
    image = NSImage.imageWithSystemSymbolName_accessibilityDescription_(symbol, title)
    image.setTemplate_(True)
    icon.setImage_(image)
    icon.setContentTintColor_(NSColor.colorWithCalibratedWhite_alpha_(0.13, 0.72))
    content.addSubview_(icon)

    text = _label(
        title,
        NSMakeRect(38, 0, frame.size.width - 50, frame.size.height),
        11.5,
        NSFontWeightMedium,
        NSColor.colorWithCalibratedWhite_alpha_(0.10, 0.84),
        vertically_centered=True,
    )
    content.addSubview_(text)

    button = NSButton.alloc().initWithFrame_(content.bounds())
    button.setTitle_("")
    button.setBordered_(False)
    button.setTarget_(target)
    button.setAction_(action)
    content.addSubview_(button)
    return button


class SettingsWindowController(NSObject):
    def initWithDelegate_preferences_(self, delegate, preferences):
        self = objc.super(SettingsWindowController, self).init()
        if self is None:
            return None

        self.delegate = delegate
        self.preferences = preferences
        self.window = None
        self.model_selector = None
        self.model_button = None
        self.model_title_label = None
        self.model_description = None
        self.model_memory = None
        self.input_selector = None
        self.input_button = None
        self.input_title_label = None
        self.shortcut_selector = None
        self.shortcut_button = None
        self.shortcut_title_label = None
        self.activation_control = None
        self.status_label = None
        self.status_dot = None
        self.sound_switch = None
        self.overlay_switch = None
        self.clipboard_switch = None
        self.start_switch = None
        self._build_window()
        return self

    def _build_window(self) -> None:
        width = 590
        height = 760
        style = NSWindowStyleMaskTitled | NSWindowStyleMaskClosable | NSWindowStyleMaskFullSizeContentView
        self.window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, width, height),
            style,
            NSBackingStoreBuffered,
            False,
        )
        self.window.setTitle_("Jarvis Dictation")
        self.window.setTitlebarAppearsTransparent_(True)
        self.window.setTitleVisibility_(1)
        self.window.setMovableByWindowBackground_(True)
        self.window.setReleasedWhenClosed_(False)
        self.window.center()

        root = NSVisualEffectView.alloc().initWithFrame_(NSMakeRect(0, 0, width, height))
        root.setMaterial_(NSVisualEffectMaterialPopover)
        root.setBlendingMode_(NSVisualEffectBlendingModeBehindWindow)
        root.setState_(NSVisualEffectStateActive)
        root.setWantsLayer_(True)
        root.layer().setBackgroundColor_(
            NSColor.colorWithCalibratedRed_green_blue_alpha_(0.93, 0.96, 0.98, 0.055).CGColor()
        )
        self.window.setContentView_(root)

        primary = NSColor.colorWithCalibratedWhite_alpha_(0.08, 0.92)
        secondary = NSColor.colorWithCalibratedWhite_alpha_(0.12, 0.60)

        title = _label("Jarvis Dictation", NSMakeRect(34, 678, 300, 34), 25, NSFontWeightBold, primary)
        subtitle = _label(
            "Local speech recognition, ready everywhere.",
            NSMakeRect(35, 653, 360, 22),
            13,
            NSFontWeightMedium,
            secondary,
        )
        root.addSubview_(title)
        root.addSubview_(subtitle)

        status_background, status_content = _glass_surface(
            NSMakeRect(395, 668, 160, 34),
            17,
            tint_alpha=0.04,
        )
        root.addSubview_(status_background)

        self.status_dot = NSView.alloc().initWithFrame_(NSMakeRect(14, 12.5, 9, 9))
        self.status_dot.setWantsLayer_(True)
        self.status_dot.layer().setCornerRadius_(4.5)
        status_content.addSubview_(self.status_dot)

        self.status_label = _label(
            "Starting",
            NSMakeRect(31, 0, 116, 34),
            12,
            NSFontWeightMedium,
            primary,
            vertically_centered=True,
        )
        status_content.addSubview_(self.status_label)
        self.status_background = status_background

        model_heading = _label("Speech model", NSMakeRect(35, 596, 180, 22), 15, NSFontWeightBold, primary)
        model_caption = _label(
            "Choose the model kept warm.",
            NSMakeRect(35, 572, 240, 20),
            12,
            NSFontWeightMedium,
            secondary,
        )
        root.addSubview_(model_heading)
        root.addSubview_(model_caption)

        self.model_selector, model_content = _glass_surface(
            NSMakeRect(290, 568, 265, 48),
            14,
            tint_alpha=0.025,
            clear=True,
        )
        root.addSubview_(self.model_selector)

        self.model_title_label = _label(
            "",
            NSMakeRect(16, 0, 216, 48),
            12.5,
            NSFontWeightMedium,
            primary,
            vertically_centered=True,
        )
        model_content.addSubview_(self.model_title_label)

        chevron = NSImageView.alloc().initWithFrame_(NSMakeRect(237, 17, 12, 14))
        chevron_image = NSImage.imageWithSystemSymbolName_accessibilityDescription_(
            "chevron.down",
            "Choose speech model",
        )
        chevron_image.setTemplate_(True)
        chevron.setImage_(chevron_image)
        chevron.setContentTintColor_(secondary)
        model_content.addSubview_(chevron)

        self.model_button = NSPopUpButton.alloc().initWithFrame_pullsDown_(model_content.bounds(), False)
        self.model_button.setBordered_(False)
        self.model_button.setAlphaValue_(0.01)
        self.model_button.setFocusRingType_(NSFocusRingTypeNone)
        self.model_button.setRefusesFirstResponder_(True)
        for preset, _, _, _ in MODEL_OPTIONS:
            self.model_button.addItemWithTitle_(MODEL_MENU_TITLE_BY_PRESET[preset])
            self.model_button.lastItem().setRepresentedObject_(preset)
        self.model_button.setTarget_(self)
        self.model_button.setAction_("modelChanged:")
        model_content.addSubview_(self.model_button)
        self._update_model_selector(self.preferences.model_preset)

        self.model_description = _label("", NSMakeRect(294, 543, 261, 20), 11, NSFontWeightMedium, secondary)
        self.model_memory = _label("", NSMakeRect(294, 524, 261, 20), 11, NSFontWeightMedium, secondary)
        root.addSubview_(self.model_description)
        root.addSubview_(self.model_memory)
        self._update_model_copy(self.preferences.model_preset)

        input_heading = _label("Input", NSMakeRect(35, 480, 180, 22), 15, NSFontWeightBold, primary)
        root.addSubview_(input_heading)

        microphone_title = _label("Microphone", NSMakeRect(35, 438, 190, 20), 13, NSFontWeightMedium, primary)
        microphone_caption = _label(
            "Use the system input or choose a specific device.",
            NSMakeRect(35, 419, 245, 18),
            10.5,
            NSFontWeightMedium,
            secondary,
        )
        root.addSubview_(microphone_title)
        root.addSubview_(microphone_caption)

        self.input_selector, input_content = _glass_surface(
            NSMakeRect(290, 414, 265, 48),
            14,
            tint_alpha=0.025,
            clear=True,
        )
        root.addSubview_(self.input_selector)
        self.input_title_label = _label(
            "",
            NSMakeRect(16, 0, 216, 48),
            12.5,
            NSFontWeightMedium,
            primary,
            vertically_centered=True,
        )
        input_content.addSubview_(self.input_title_label)

        input_chevron = NSImageView.alloc().initWithFrame_(NSMakeRect(237, 17, 12, 14))
        input_chevron_image = NSImage.imageWithSystemSymbolName_accessibilityDescription_(
            "chevron.down",
            "Choose microphone",
        )
        input_chevron_image.setTemplate_(True)
        input_chevron.setImage_(input_chevron_image)
        input_chevron.setContentTintColor_(secondary)
        input_content.addSubview_(input_chevron)

        self.input_button = NSPopUpButton.alloc().initWithFrame_pullsDown_(input_content.bounds(), False)
        self.input_button.setBordered_(False)
        self.input_button.setAlphaValue_(0.01)
        self.input_button.setFocusRingType_(NSFocusRingTypeNone)
        self.input_button.setRefusesFirstResponder_(True)
        self.input_button.setTarget_(self)
        self.input_button.setAction_("inputDeviceChanged:")
        input_content.addSubview_(self.input_button)
        self._refresh_input_devices()

        shortcut_title = _label("Shortcut", NSMakeRect(35, 380, 190, 20), 13, NSFontWeightMedium, primary)
        shortcut_caption = _label(
            "Choose the global key that controls dictation.",
            NSMakeRect(35, 361, 245, 18),
            10.5,
            NSFontWeightMedium,
            secondary,
        )
        root.addSubview_(shortcut_title)
        root.addSubview_(shortcut_caption)

        self.shortcut_selector, shortcut_content = _glass_surface(
            NSMakeRect(290, 356, 265, 48),
            14,
            tint_alpha=0.025,
            clear=True,
        )
        root.addSubview_(self.shortcut_selector)
        self.shortcut_title_label = _label(
            "",
            NSMakeRect(16, 0, 216, 48),
            12.5,
            NSFontWeightMedium,
            primary,
            vertically_centered=True,
        )
        shortcut_content.addSubview_(self.shortcut_title_label)

        shortcut_icon = NSImageView.alloc().initWithFrame_(NSMakeRect(235, 16, 16, 16))
        shortcut_icon_image = NSImage.imageWithSystemSymbolName_accessibilityDescription_(
            "keyboard",
            "Record dictation shortcut",
        )
        shortcut_icon_image.setTemplate_(True)
        shortcut_icon.setImage_(shortcut_icon_image)
        shortcut_icon.setContentTintColor_(secondary)
        shortcut_content.addSubview_(shortcut_icon)

        self.shortcut_button = NSButton.alloc().initWithFrame_(shortcut_content.bounds())
        self.shortcut_button.setTitle_("")
        self.shortcut_button.setBordered_(False)
        self.shortcut_button.setFocusRingType_(NSFocusRingTypeNone)
        self.shortcut_button.setRefusesFirstResponder_(True)
        self.shortcut_button.setTarget_(self)
        self.shortcut_button.setAction_("recordShortcut:")
        shortcut_content.addSubview_(self.shortcut_button)
        self._update_shortcut_selector()

        activation_title = _label("Activation", NSMakeRect(35, 320, 190, 20), 13, NSFontWeightMedium, primary)
        activation_caption = _label(
            "Toggle dictation or hold the shortcut while speaking.",
            NSMakeRect(35, 301, 245, 18),
            10.5,
            NSFontWeightMedium,
            secondary,
        )
        root.addSubview_(activation_title)
        root.addSubview_(activation_caption)

        self.activation_control = NSSegmentedControl.alloc().initWithFrame_(NSMakeRect(290, 302, 265, 32))
        self.activation_control.setSegmentCount_(2)
        self.activation_control.setLabel_forSegment_("Toggle", 0)
        self.activation_control.setLabel_forSegment_("Hold", 1)
        self.activation_control.setSegmentStyle_(NSSegmentStyleCapsule)
        self.activation_control.setSelectedSegment_(1 if self.preferences.activation_mode == "hold" else 0)
        self.activation_control.setFocusRingType_(NSFocusRingTypeNone)
        self.activation_control.setRefusesFirstResponder_(True)
        self.activation_control.setTarget_(self)
        self.activation_control.setAction_("activationModeChanged:")
        root.addSubview_(self.activation_control)

        general_heading = _label("Behavior", NSMakeRect(35, 258, 180, 22), 15, NSFontWeightBold, primary)
        root.addSubview_(general_heading)

        rows = (
            ("Sound cues", "Play a quiet sound when dictation starts and stops.", "sound_switch", self.preferences.play_sounds),
            ("Recording overlay", "Show the glass waveform while listening.", "overlay_switch", self.preferences.show_overlay),
            (
                "Preserve clipboard",
                "Restore clipboard contents after inserting dictated text.",
                "clipboard_switch",
                self.preferences.preserve_clipboard,
            ),
            (
                "Warm model on launch",
                "Start the selected model automatically when Jarvis opens.",
                "start_switch",
                self.preferences.start_model_on_launch,
            ),
        )
        row_y = 220
        for title_text, caption_text, attribute, enabled in rows:
            root.addSubview_(_label(title_text, NSMakeRect(35, row_y, 190, 20), 13, NSFontWeightMedium, primary))
            root.addSubview_(_label(caption_text, NSMakeRect(35, row_y - 19, 390, 18), 10.5, NSFontWeightMedium, secondary))
            switch = NSSwitch.alloc().initWithFrame_(NSMakeRect(500, row_y - 3, 44, 24))
            switch.setFocusRingType_(NSFocusRingTypeNone)
            switch.setRefusesFirstResponder_(True)
            switch.setState_(NSControlStateValueOn if enabled else 0)
            switch.setTarget_(self)
            switch.setAction_("preferenceChanged:")
            setattr(self, attribute, switch)
            root.addSubview_(switch)
            row_y -= 46

        _glass_button(
            root,
            NSMakeRect(34, 14, 160, 34),
            "Accessibility",
            "hand.raised.fill",
            self,
            "openPrivacySettings:",
        )
        _glass_button(
            root,
            NSMakeRect(204, 14, 130, 34),
            "App Log",
            "doc.text",
            self,
            "openModelLog:",
        )
        _glass_button(
            root,
            NSMakeRect(411, 14, 144, 34),
            "Restart Model",
            "arrow.clockwise",
            self,
            "restartModel:",
        )

    def show(self) -> None:
        self._refresh_input_devices()
        self.window.center()
        self.window.makeKeyAndOrderFront_(None)
        NSApplication.sharedApplication().activateIgnoringOtherApps_(True)

    def setStatus_message_(self, state: str, message: str) -> None:
        if self.status_label is None:
            return
        compact = message.replace(" speech model", "").replace("Model ", "")
        if state == "ready" and compact.endswith(" ready"):
            compact = compact.removesuffix(" ready")
        if len(compact) > 20:
            compact = compact[:19] + "..."
        self.status_label.setStringValue_(compact)
        color = {
            "ready": (0.16, 0.58, 0.37, 0.95),
            "recording": (0.10, 0.42, 0.62, 0.95),
            "loading": (0.64, 0.48, 0.10, 0.95),
            "switching": (0.64, 0.48, 0.10, 0.95),
            "error": (0.72, 0.20, 0.22, 0.95),
        }.get(state, (0.35, 0.38, 0.40, 0.80))
        self.status_dot.layer().setBackgroundColor_(
            NSColor.colorWithCalibratedRed_green_blue_alpha_(*color).CGColor()
        )

    def setModelControlsEnabled_(self, enabled: bool) -> None:
        self.model_button.setEnabled_(enabled)
        self.model_selector.layer().setOpacity_(1.0 if enabled else 0.55)
        self.input_button.setEnabled_(enabled)
        self.input_selector.layer().setOpacity_(1.0 if enabled else 0.55)
        self.shortcut_button.setEnabled_(enabled)
        self.shortcut_selector.layer().setOpacity_(1.0 if enabled else 0.55)
        self.activation_control.setEnabled_(enabled)

    def modelChanged_(self, sender) -> None:  # noqa: ANN001
        preset = str(sender.selectedItem().representedObject())
        self._update_model_selector(preset)
        self._update_model_copy(preset)
        accepted = self.delegate.select_model_preset(preset)
        if not accepted:
            self._update_model_selector(self.preferences.model_preset)
            self._update_model_copy(self.preferences.model_preset)

    def preferenceChanged_(self, sender) -> None:  # noqa: ANN001
        self.preferences.play_sounds = self.sound_switch.state() == NSControlStateValueOn
        self.preferences.show_overlay = self.overlay_switch.state() == NSControlStateValueOn
        self.preferences.preserve_clipboard = self.clipboard_switch.state() == NSControlStateValueOn
        self.preferences.start_model_on_launch = self.start_switch.state() == NSControlStateValueOn
        self.preferences.synchronize()
        self.delegate.apply_preferences()

    def inputDeviceChanged_(self, sender) -> None:  # noqa: ANN001
        selected = sender.selectedItem()
        self.preferences.input_device = str(selected.representedObject() or "")
        self.preferences.synchronize()
        self._update_input_selector()
        self.delegate.apply_preferences()

    def activationModeChanged_(self, sender) -> None:  # noqa: ANN001
        self.preferences.activation_mode = "hold" if sender.selectedSegment() == 1 else "toggle"
        self.preferences.synchronize()
        self.delegate.apply_preferences()

    def recordShortcut_(self, sender) -> None:  # noqa: ANN001
        self.shortcut_title_label.setStringValue_("Press any key...")
        self.shortcut_button.setEnabled_(False)
        self.delegate.begin_shortcut_capture()

    def complete_shortcut_capture(self, shortcut: str | None) -> None:
        if shortcut is not None:
            self.preferences.shortcut = shortcut
            self.preferences.synchronize()
            self.delegate.apply_preferences()
        self.shortcut_button.setEnabled_(True)
        self._update_shortcut_selector()

    def restartModel_(self, sender) -> None:  # noqa: ANN001
        self.delegate.restart_model()

    def openPrivacySettings_(self, sender) -> None:  # noqa: ANN001
        url = NSURL.URLWithString_(
            "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"
        )
        NSWorkspace.sharedWorkspace().openURL_(url)

    def openModelLog_(self, sender) -> None:  # noqa: ANN001
        NSWorkspace.sharedWorkspace().openURL_(NSURL.fileURLWithPath_(str(APP_LOG_PATH)))

    def _update_model_copy(self, preset: str) -> None:
        for option_preset, _, description, memory in MODEL_OPTIONS:
            if option_preset == preset:
                self.model_description.setStringValue_(description)
                self.model_memory.setStringValue_(memory)
                return

    def _update_model_selector(self, preset: str) -> None:
        title = MODEL_MENU_TITLE_BY_PRESET.get(preset, MODEL_TITLE_BY_PRESET.get(preset, preset))
        self.model_title_label.setStringValue_(title)
        self.model_button.selectItemWithTitle_(title)

    def _refresh_input_devices(self) -> None:
        selected_device = self.preferences.input_device
        self.input_button.removeAllItems()
        self.input_button.addItemWithTitle_("System Default")
        self.input_button.lastItem().setRepresentedObject_("")
        for device_name in available_input_devices():
            self.input_button.addItemWithTitle_(device_name)
            self.input_button.lastItem().setRepresentedObject_(device_name)

        if selected_device and self.input_button.itemWithTitle_(selected_device) is None:
            self.input_button.addItemWithTitle_(selected_device)
            self.input_button.lastItem().setRepresentedObject_(selected_device)
        self._update_input_selector()

    def _update_input_selector(self) -> None:
        device_name = self.preferences.input_device
        title = device_name or "System Default"
        self.input_title_label.setStringValue_(title)
        self.input_button.selectItemWithTitle_(title)

    def _update_shortcut_selector(self) -> None:
        self.shortcut_title_label.setStringValue_(shortcut_display_name(self.preferences.shortcut))



class JarvisAppDelegate(NSObject):
    def init(self):
        self = objc.super(JarvisAppDelegate, self).init()
        if self is None:
            return None

        self.preferences = None
        self.overlay = None
        self.controller = None
        self.hotkey = None
        self.model_manager = None
        self.settings = None
        self.status_item = None
        self.status_menu_item = None
        self.toggle_menu_item = None
        self.restart_menu_item = None
        self.dictation_state = "offline"
        self.model_state = "stopped"
        self.model_message = "Model stopped"
        self.shutting_down = False
        return self

    def applicationDidFinishLaunching_(self, notification) -> None:  # noqa: ANN001
        self.preferences = Preferences()
        running_info = get_server_info()
        if not self.preferences.has_saved_model_preset and running_info is not None:
            running_preset = preset_for_model_name(str(running_info.get("model_name", "")))
            if running_preset is not None:
                self.preferences.model_preset = running_preset
                self.preferences.synchronize()
        self.overlay = FloatingOverlay()
        self.controller = DictationController(
            self.overlay,
            on_state=self._controller_state_changed,
            show_overlay=self.preferences.show_overlay,
            play_sounds=self.preferences.play_sounds,
            preserve_clipboard=self.preferences.preserve_clipboard,
            input_device=self.preferences.input_device,
        )
        self.hotkey = RightCommandHotkey(
            self.controller.toggle,
            on_start=self.controller.request_start,
            on_stop=self.controller.request_stop,
            activation_mode=self.preferences.activation_mode,
            shortcut=self.preferences.shortcut,
        )
        self.model_manager = ModelManager(status_callback=self._model_status_changed)
        self.settings = SettingsWindowController.alloc().initWithDelegate_preferences_(self, self.preferences)

        self._build_status_item()
        self.hotkey.start()
        request_accessibility_permission()
        threading.Thread(
            target=request_macos_permissions,
            kwargs={"include_accessibility": False},
            daemon=True,
        ).start()

        if self.preferences.start_model_on_launch:
            self.model_manager.start_async(self.preferences.model_preset)
        else:
            self._apply_status("stopped", "Model stopped")

        logging.info("Jarvis Dictation menu-bar app started")

    def applicationWillTerminate_(self, notification) -> None:  # noqa: ANN001
        if self.shutting_down:
            return
        self.shutting_down = True
        if self.hotkey is not None:
            self.hotkey.stop()
        if self.controller is not None:
            self.controller.shutdown()
        if self.model_manager is not None:
            self.model_manager.stop()

    def applicationShouldTerminateAfterLastWindowClosed_(self, app) -> bool:  # noqa: ANN001
        return False

    def _build_status_item(self) -> None:
        self.status_item = NSStatusBar.systemStatusBar().statusItemWithLength_(NSVariableStatusItemLength)
        button = self.status_item.button()
        image = NSImage.imageWithSystemSymbolName_accessibilityDescription_("waveform", "Jarvis Dictation")
        image.setTemplate_(True)
        button.setImage_(image)
        button.setToolTip_("Jarvis Dictation")

        menu = NSMenu.alloc().init()
        self.status_menu_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("Starting...", None, "")
        self.status_menu_item.setEnabled_(False)
        menu.addItem_(self.status_menu_item)
        menu.addItem_(NSMenuItem.separatorItem())

        self.toggle_menu_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Start Dictation",
            "toggleDictation:",
            "",
        )
        self.toggle_menu_item.setTarget_(self)
        menu.addItem_(self.toggle_menu_item)

        settings_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("Settings...", "showSettings:", ",")
        settings_item.setTarget_(self)
        menu.addItem_(settings_item)

        self.restart_menu_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Restart Model",
            "restartModel:",
            "",
        )
        self.restart_menu_item.setTarget_(self)
        menu.addItem_(self.restart_menu_item)

        menu.addItem_(NSMenuItem.separatorItem())
        quit_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("Quit Jarvis Dictation", "quit:", "q")
        quit_item.setTarget_(self)
        menu.addItem_(quit_item)
        self.status_item.setMenu_(menu)

    def toggleDictation_(self, sender) -> None:  # noqa: ANN001
        self.controller.toggle()

    def showSettings_(self, sender) -> None:  # noqa: ANN001
        self.settings.show()

    def restartModel_(self, sender) -> None:  # noqa: ANN001
        self.restart_model()

    def quit_(self, sender) -> None:  # noqa: ANN001
        NSApplication.sharedApplication().terminate_(self)

    def select_model_preset(self, preset: str) -> bool:
        if self.dictation_state in {"recording", "connecting", "finalizing"}:
            self._apply_status("error", "Stop dictation before switching models")
            return False
        if preset not in MODEL_PRESETS:
            return False

        self.preferences.model_preset = preset
        self.preferences.synchronize()
        self.controller.disconnect_transcriber()
        self.model_manager.restart_async(preset)
        return True

    def restart_model(self) -> None:
        if self.dictation_state in {"recording", "connecting", "finalizing"}:
            self._apply_status("error", "Stop dictation before restarting the model")
            return
        self.controller.disconnect_transcriber()
        self.model_manager.restart_async(self.preferences.model_preset)

    def begin_shortcut_capture(self) -> None:
        if self.dictation_state in {"recording", "connecting", "finalizing"}:
            self.settings.complete_shortcut_capture(None)
            self._apply_status("error", "Stop dictation before changing the shortcut")
            return
        self.hotkey.begin_capture(self._shortcut_captured)

    def _shortcut_captured(self, shortcut: str | None) -> None:
        AppHelper.callAfter(self.settings.complete_shortcut_capture, shortcut)

    def apply_preferences(self) -> None:
        self.controller.apply_preferences(
            show_overlay=self.preferences.show_overlay,
            play_sounds=self.preferences.play_sounds,
            preserve_clipboard=self.preferences.preserve_clipboard,
            input_device=self.preferences.input_device,
        )
        self.hotkey.set_activation_mode(self.preferences.activation_mode)
        self.hotkey.set_shortcut(self.preferences.shortcut)

    def _model_status_changed(self, state: str, message: str, info: dict | None) -> None:
        AppHelper.callAfter(self._handle_model_status, state, message, info)

    def _handle_model_status(self, state: str, message: str, info: dict | None) -> None:
        self.model_state = state
        self.model_message = message
        if state == "ready":
            self.controller.disconnect_transcriber()
            self.controller.preload()
        elif state in {"loading", "switching", "stopped", "error"}:
            self.controller.disconnect_transcriber()
        self._apply_status(state, message)

    def _controller_state_changed(self, state: str) -> None:
        AppHelper.callAfter(self._handle_controller_state, state)

    def _handle_controller_state(self, state: str) -> None:
        self.dictation_state = state
        if state == "recording":
            self._apply_status("recording", "Listening")
        elif state == "finalizing":
            self._apply_status("finalizing", "Finalizing")
        elif state == "error":
            self._apply_status("error", "Microphone error")
        elif state == "permission":
            self._apply_status("error", "Accessibility required for paste")
        elif state == "offline":
            self._apply_status(self.model_state, self.model_message)
        elif state == "ready" and self.model_state == "ready":
            self._apply_status("ready", self.model_message)

    def _apply_status(self, state: str, message: str) -> None:
        if self.status_menu_item is None:
            return

        self.status_menu_item.setTitle_(message)
        is_ready = self.model_state == "ready" and state not in {"loading", "switching", "error"}
        is_recording = state in {"recording", "finalizing"}
        self.toggle_menu_item.setEnabled_(is_ready or is_recording)
        self.toggle_menu_item.setTitle_("Stop Dictation" if is_recording else "Start Dictation")
        self.restart_menu_item.setEnabled_(not is_recording and state not in {"loading", "switching"})
        self.settings.setStatus_message_(state, message)
        self.settings.setModelControlsEnabled_(not is_recording and state not in {"loading", "switching"})

        symbol = "waveform.circle.fill" if state == "recording" else "waveform"
        image = NSImage.imageWithSystemSymbolName_accessibilityDescription_(symbol, message)
        image.setTemplate_(True)
        self.status_item.button().setImage_(image)
        self.status_item.button().setToolTip_(message)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the native Jarvis Dictation menu-bar application.")
    parser.add_argument("--debug", action="store_true")
    return parser.parse_args()


_delegate = None


def main() -> None:
    global _delegate

    args = parse_args()
    log_level = logging.DEBUG if args.debug else logging.INFO
    log_format = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(log_format)
    root_logger.addHandler(console_handler)

    APP_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    file_handler = RotatingFileHandler(APP_LOG_PATH, maxBytes=1_000_000, backupCount=3)
    file_handler.setFormatter(log_format)
    root_logger.addHandler(file_handler)

    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
    _delegate = JarvisAppDelegate.alloc().init()
    app.setDelegate_(_delegate)

    def terminate(signum, frame) -> None:  # noqa: ANN001
        logging.info("Received signal %s; quitting Jarvis Dictation", signum)
        AppHelper.callAfter(app.terminate_, None)

    signal.signal(signal.SIGINT, terminate)
    signal.signal(signal.SIGTERM, terminate)
    AppHelper.runEventLoop(installInterrupt=True)


if __name__ == "__main__":
    main()
