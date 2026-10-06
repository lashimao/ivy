#!/usr/bin/env python3
"""Build only; does not start any service or change login items."""
import argparse, pathlib, plistlib, subprocess
p=argparse.ArgumentParser();p.add_argument('--output',type=pathlib.Path,default=pathlib.Path.home()/'Applications/我的任务.app');p.add_argument('--demo-data',type=pathlib.Path);a=p.parse_args()
here=pathlib.Path(__file__).resolve().parent
contents=a.output/'Contents';(contents/'MacOS').mkdir(parents=True,exist_ok=True)
subprocess.run(['swiftc',str(here/'TaskBoard.swift'),str(here/'SpeechInput.swift'),str(here/'ProjectViews.swift'),'-o',str(contents/'MacOS/TaskBoard'),'-framework','Cocoa','-framework','SwiftUI','-framework','Speech','-framework','AVFoundation'],check=True)
info=dict(CFBundleDisplayName='我的任务',CFBundleExecutable='TaskBoard',CFBundleIdentifier='com.lashimao.ivy-task-board',CFBundleName='我的任务',CFBundlePackageType='APPL',CFBundleVersion='4',LSMinimumSystemVersion='14.0',LSUIElement=True,NSHighResolutionCapable=True,IvySourceRoot=str(here.parents[2]),NSSpeechRecognitionUsageDescription='将你点击麦克风后说的话转成可编辑的任务草稿。',NSMicrophoneUsageDescription='仅在点击语音输入时录音，停止后结束采集。')
if a.demo_data:info.update(CFBundleIdentifier='com.lashimao.ivy-demo',CFBundleDisplayName='Ivy演示',CFBundleName='Ivy演示',IvyDemo=True,IvyBoardData=str(a.demo_data.resolve()))
(contents/'Info.plist').write_bytes(plistlib.dumps(info))
subprocess.run(['codesign','--force','--deep','--sign','-',str(a.output)],check=True)
print(a.output)
