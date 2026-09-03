[app]
title = PoW Lab Pro
package.name = powlabpro
package.domain = org.mining
source.dir = .
source.include_exts = py,png,jpg,kv,atlas
version = 1.0
requirements = python3,kivy,requests,beautifulsoup4,urllib3,certifi,charset_normalizer,idna
orientation = portrait
android.permissions = INTERNET
android.archs = arm64-v8a
android.allow_backup = True
android.accept_sdk_license = True

[buildozer]
log_level = 2
warn_on_root = 0
