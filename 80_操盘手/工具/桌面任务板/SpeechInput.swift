import Cocoa
import Speech
import AVFoundation

/// Records only after a click. Transcription updates the draft; it never submits a task.
final class SpeechInput: ObservableObject {
    @Published var text = ""
    @Published var note = ""
    @Published var recording = false
    @Published var requesting = false
    @Published var failed = false
    private let engine = AVAudioEngine()
    private let recognizer = SFSpeechRecognizer(locale: Locale(identifier: "zh-CN"))
    private var request: SFSpeechAudioBufferRecognitionRequest?
    private var task: SFSpeechRecognitionTask?
    private var tapped = false
    private var generation = UUID()
    private var timer: Timer?

    func start() {
        guard !recording, !requesting else { return }
        requesting = true; failed = false; note = "正在检查语音权限…"
        SFSpeechRecognizer.requestAuthorization { state in
            DispatchQueue.main.async {
                guard state == .authorized else {
                    self.fail("请在系统设置 → 隐私与安全性 → 语音识别中允许“我的任务”")
                    return
                }
                AVCaptureDevice.requestAccess(for: .audio) { allowed in
                    DispatchQueue.main.async {
                        guard allowed else {
                            self.fail("请在系统设置 → 隐私与安全性 → 麦克风中允许“我的任务”")
                            return
                        }
                        self.begin()
                    }
                }
            }
        }
    }
    private func begin() {
        guard let recognizer, recognizer.isAvailable else { fail("语音识别暂不可用，请稍后重试"); return }
        task?.cancel(); task = nil
        generation = UUID(); let token = generation
        let req = SFSpeechAudioBufferRecognitionRequest()
        req.shouldReportPartialResults = true
        req.requiresOnDeviceRecognition = recognizer.supportsOnDeviceRecognition
        request = req; text = ""
        let input = engine.inputNode
        let format = input.outputFormat(forBus: 0)
        guard format.sampleRate > 0, format.channelCount > 0 else { fail("未找到可用麦克风"); return }
        input.installTap(onBus: 0, bufferSize: 1024, format: format) { buffer, _ in req.append(buffer) }
        tapped = true
        task = recognizer.recognitionTask(with: req) { result, error in
            DispatchQueue.main.async {
                guard self.generation == token else { return }
                if let result { self.text = result.bestTranscription.formattedString }
                if result?.isFinal == true { self.finish(); self.generation = UUID(); self.note = self.text.isEmpty ? "没有识别到语音" : "已转成文字" }
                else if let error { NSLog("Ivy speech error: %@ %ld", (error as NSError).domain, (error as NSError).code); self.finish(); self.generation = UUID(); self.failed = self.text.isEmpty; self.note = self.text.isEmpty ? "未识别到语音，请检查麦克风或网络后重试" : "识别结束，文字已保留" }
            }
        }
        do {
            engine.prepare(); try engine.start(); recording = true; requesting = false
            note = req.requiresOnDeviceRecognition ? "正在聆听 · 本机识别" : "正在聆听 · Apple 在线识别"
            timer = Timer.scheduledTimer(withTimeInterval: 60, repeats: false) { [weak self] _ in self?.stop() }
        } catch { task?.cancel(); fail("麦克风启动失败，请检查输入设备") }
    }
    func stop() {
        guard recording else { return }
        finish(); requesting = true; note = "正在整理文字…"
        let token = generation
        timer = Timer.scheduledTimer(withTimeInterval: 3, repeats: false) { [weak self] _ in
            guard let self, self.generation == token else { return }
            self.generation = UUID(); self.task?.cancel(); self.task = nil; self.requesting = false
            self.note = self.text.isEmpty ? "没有识别到语音" : "已转成文字"
        }
    }
    private func finish() {
        timer?.invalidate(); timer = nil
        engine.stop()
        if tapped { engine.inputNode.removeTap(onBus: 0); tapped = false }
        request?.endAudio(); request = nil; recording = false; requesting = false
    }
    private func fail(_ message: String) { finish(); failed = true; note = message }
}

/// Deterministic acceptance hook: an explicitly supplied audio file, never the microphone.
func runSpeechFileProbe(_ path: String) {
    let recognizer = SFSpeechRecognizer(locale: Locale(identifier: "zh-CN"))!
    var output: [String:Any] = ["authorization":SFSpeechRecognizer.authorizationStatus().rawValue,"available":recognizer.isAvailable,"on_device":recognizer.supportsOnDeviceRecognition]
    guard SFSpeechRecognizer.authorizationStatus() == .authorized else {
        print(String(data:try! JSONSerialization.data(withJSONObject:output),encoding:.utf8)!); return
    }
    let request = SFSpeechURLRecognitionRequest(url: URL(fileURLWithPath:path))
    request.requiresOnDeviceRecognition = recognizer.supportsOnDeviceRecognition
    var finished = false
    let task = recognizer.recognitionTask(with:request) { result,error in
        if let result { output["text"] = result.bestTranscription.formattedString; output["final"] = result.isFinal; if result.isFinal { finished = true } }
        if let error { output["error_domain"] = (error as NSError).domain; output["error_code"] = (error as NSError).code; finished = true }
    }
    let deadline = Date().addingTimeInterval(30)
    while !finished && Date() < deadline { RunLoop.current.run(until:Date().addingTimeInterval(0.1)) }
    task.cancel(); output["finished"] = finished
    print(String(data:try! JSONSerialization.data(withJSONObject:output),encoding:.utf8)!)
}
