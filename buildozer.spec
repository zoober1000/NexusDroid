[app]

# ── Basic identity ────────────────────────────────────────────────────────────
title           = NexusDroid
package.name    = nexusdroid
package.domain  = org.nexusdroid
source.dir      = .
source.include_exts = py,png,jpg,kv,atlas

version         = 1.0

# ── Dependencies ──────────────────────────────────────────────────────────────
requirements = python3,kivy,android,pyjnius,psutil,requests

# ── Orientation / UI ──────────────────────────────────────────────────────────
orientation     = portrait
fullscreen      = 0

# ── Android SDK targets ───────────────────────────────────────────────────────
android.minapi          = 21
android.api             = 34
android.ndk             = 25b
android.archs           = arm64-v8a, armeabi-v7a

# ── AndroidManifest permissions ───────────────────────────────────────────────
# Every permission listed here is written into AndroidManifest.xml.
# Runtime request_permissions() alone is NOT enough — Android will silently
# refuse to show the dialog for any permission not declared in the manifest.
#
# Android 6–12  → READ/WRITE_EXTERNAL_STORAGE
# Android 13+   → READ_MEDIA_IMAGES / READ_MEDIA_VIDEO / READ_MEDIA_AUDIO
#                 (READ_EXTERNAL_STORAGE is ignored on API 33+, but harmless)
# All versions  → REQUEST_INSTALL_PACKAGES  (App Manager screen)
#
# Declare ALL of them so the single APK works across the full API range.
android.permissions = \
    READ_EXTERNAL_STORAGE, \
    WRITE_EXTERNAL_STORAGE, \
    READ_MEDIA_IMAGES, \
    READ_MEDIA_VIDEO, \
    READ_MEDIA_AUDIO, \
    REQUEST_INSTALL_PACKAGES, \
    INTERNET, \
    ACCESS_NETWORK_STATE

# ── APK features ──────────────────────────────────────────────────────────────
# Declare that we install packages so the OS grants the privilege correctly.
android.manifest_intent_filters =

# Allow the user to install APKs (required alongside the permission above).
# Buildozer exposes this via the uses-permission tag; the matching
# uses-feature is added automatically by the Android build tools.
android.add_activities =

# ── Buildozer / p4a internals ─────────────────────────────────────────────────
[buildozer]
log_level = 2
warn_on_root = 1
