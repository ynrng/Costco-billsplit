// Called by autosplit.py. All recognition happens locally using Apple Vision.
import Foundation
import Vision
import ImageIO

func recognize(_ path: String, leftCrop: Double) throws -> [[String: Any]] {
    let url = URL(fileURLWithPath: path)
    guard let source = CGImageSourceCreateWithURL(url as CFURL, nil),
          let image = CGImageSourceCreateImageAtIndex(source, 0, nil) else {
        throw NSError(domain: "ReceiptOCR", code: 1, userInfo: [NSLocalizedDescriptionKey: "Cannot read image: \(path)"])
    }
    let properties = CGImageSourceCopyPropertiesAtIndex(source, 0, nil) as? [CFString: Any]
    let orientationValue = (properties?[kCGImagePropertyOrientation] as? UInt32) ?? 1
    let orientation = CGImagePropertyOrientation(rawValue: orientationValue) ?? .up
    let request = VNRecognizeTextRequest()
    request.recognitionLevel = .accurate
    request.usesLanguageCorrection = false
    request.recognitionLanguages = ["en-GB"]
    // Ignore the handwritten left margin before OCR. Vision returns boxes
    // relative to this region; transform them back to full-image coordinates.
    request.regionOfInterest = CGRect(x: leftCrop, y: 0, width: 1-leftCrop, height: 1)
    try VNImageRequestHandler(cgImage: image, orientation: orientation, options: [:]).perform([request])
    return (request.results ?? []).compactMap { result in
        guard let candidate = result.topCandidates(1).first else { return nil }
        let box = result.boundingBox
        return ["text": candidate.string, "confidence": candidate.confidence,
                "x": leftCrop + Double(box.minX) * (1-leftCrop), "y": Double(1-box.maxY),
                "width": Double(box.width) * (1-leftCrop), "height": Double(box.height)]
    }
}

do {
    guard CommandLine.arguments.count >= 4, let leftCrop = Double(CommandLine.arguments[2]) else {
        throw NSError(domain: "ReceiptOCR", code: 2, userInfo: [NSLocalizedDescriptionKey: "Usage: receipt_ocr.swift OUTPUT_DIR LEFT_CROP IMAGE..."])
    }
    let output = URL(fileURLWithPath: CommandLine.arguments[1])
    for path in CommandLine.arguments.dropFirst(3) {
        let url = URL(fileURLWithPath: path)
        let observations = try recognize(path, leftCrop: leftCrop)
        // Python adds the exact nanosecond timestamp after successful OCR.
        let data = try JSONSerialization.data(withJSONObject: ["left_crop": leftCrop, "coordinate_system": "full_image", "observations": observations], options: [.prettyPrinted, .sortedKeys])
        try data.write(to: output.appendingPathComponent(url.lastPathComponent + ".json"), options: .atomic)
        let text = observations.sorted { ($0["y"] as! Double) < ($1["y"] as! Double) }
            .map { $0["text"] as! String }.joined(separator: "\n")
        try text.write(to: output.appendingPathComponent(url.lastPathComponent + ".txt"), atomically: true, encoding: .utf8)
        print("OCR: \(url.lastPathComponent)")
    }
} catch {
    FileHandle.standardError.write(Data("\(error.localizedDescription)\n".utf8))
    exit(1)
}
