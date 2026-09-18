[app]
title = ArcadeClip
project_dir = ..
input_file = run/ArcadeClip.py
exec_directory = dist
project_file =
icon = src/assets/icon.ico

[python]
python_path =
packages = Nuitka==4.2.1

[qt]
qml_files =
excluded_qml_plugins =
modules = Core,Gui,Widgets,Multimedia
plugins = platforms,multimedia

[nuitka]
mode = standalone
extra_args = --quiet --windows-console-mode=attach --include-data-dir=src/assets=assets --include-data-dir=docs/manual=manual --include-data-dir=src/locales=locales --include-data-files=src/licenses.json=licenses.json --nofollow-import-to=yt_dlp.extractor._extractors --nofollow-import-to=yt_dlp.extractor.lazy_extractors --include-package=yt_dlp.downloader --include-package=yt_dlp.postprocessor --include-package=yt_dlp.networking --include-package=yt_dlp.extractor.youtube --include-module=yt_dlp.extractor.generic --include-package=yt_dlp_ejs --include-package-data=yt_dlp_ejs --include-package=rapidocr --include-package-data=rapidocr --include-package=onnxruntime --include-package-data=onnxruntime --include-package-data=certifi --include-distribution-metadata=yt-dlp --include-distribution-metadata=yt-dlp-ejs --noinclude-dlls=cv2/opencv_videoio_ffmpeg*.dll --assume-yes-for-downloads --report=.validation-data/windows-build-report.xml
