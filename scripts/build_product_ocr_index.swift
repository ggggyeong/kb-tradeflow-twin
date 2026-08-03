import AppKit
import CryptoKit
import Foundation
import PDFKit
import Vision

struct OCRPage: Codable {
    let sourceFile: String
    let sourceSha256: String
    let page: Int
    let text: String
}

struct OCRIndex: Codable {
    let schemaVersion: String
    let generatedAt: String
    let pages: [OCRPage]
}

guard CommandLine.arguments.count == 3 else {
    fputs("usage: build_product_ocr_index.swift <pdf-directory> <output-json>\n", stderr)
    exit(2)
}

let sourceDirectory = URL(fileURLWithPath: CommandLine.arguments[1], isDirectory: true)
let outputURL = URL(fileURLWithPath: CommandLine.arguments[2])
let manager = FileManager.default
let pdfURLs = try manager.contentsOfDirectory(
    at: sourceDirectory,
    includingPropertiesForKeys: nil,
    options: [.skipsHiddenFiles]
).filter { $0.pathExtension.lowercased() == "pdf" }
 .sorted { $0.lastPathComponent.localizedStandardCompare($1.lastPathComponent) == .orderedAscending }

var indexedPages: [OCRPage] = []

for pdfURL in pdfURLs {
    let data = try Data(contentsOf: pdfURL)
    let digest = SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
    guard let document = PDFDocument(url: pdfURL) else {
        fputs("failed to open \(pdfURL.path)\n", stderr)
        continue
    }
    for pageIndex in 0..<document.pageCount {
        guard let page = document.page(at: pageIndex) else { continue }
        let bounds = page.bounds(for: .mediaBox)
        let scale: CGFloat = 2.0
        let pixelWidth = max(1, Int(bounds.width * scale))
        let pixelHeight = max(1, Int(bounds.height * scale))
        guard
            let context = CGContext(
                data: nil,
                width: pixelWidth,
                height: pixelHeight,
                bitsPerComponent: 8,
                bytesPerRow: 0,
                space: CGColorSpaceCreateDeviceRGB(),
                bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue
            )
        else {
            continue
        }
        context.setFillColor(NSColor.white.cgColor)
        context.fill(CGRect(x: 0, y: 0, width: pixelWidth, height: pixelHeight))
        context.saveGState()
        context.scaleBy(x: scale, y: scale)
        page.draw(with: .mediaBox, to: context)
        context.restoreGState()
        guard let image = context.makeImage() else { continue }

        let request = VNRecognizeTextRequest()
        request.recognitionLevel = .accurate
        request.usesLanguageCorrection = true
        request.recognitionLanguages = ["ko-KR", "en-US"]
        let handler = VNImageRequestHandler(cgImage: image, options: [:])
        do {
            try handler.perform([request])
            let text = (request.results ?? []).compactMap {
                $0.topCandidates(1).first?.string
            }.joined(separator: "\n")
            indexedPages.append(
                OCRPage(
                    sourceFile: pdfURL.lastPathComponent.precomposedStringWithCanonicalMapping,
                    sourceSha256: digest,
                    page: pageIndex + 1,
                    text: text
                )
            )
            fputs(
                "indexed \(pdfURL.lastPathComponent) page \(pageIndex + 1)/\(document.pageCount)\n",
                stderr
            )
        } catch {
            fputs(
                "OCR failed \(pdfURL.lastPathComponent) page \(pageIndex + 1): \(error)\n",
                stderr
            )
        }
    }
}

let formatter = ISO8601DateFormatter()
let index = OCRIndex(
    schemaVersion: "product-ocr-index-v1",
    generatedAt: formatter.string(from: Date()),
    pages: indexedPages
)
let encoder = JSONEncoder()
encoder.outputFormatting = [.prettyPrinted, .sortedKeys, .withoutEscapingSlashes]
try encoder.encode(index).write(to: outputURL, options: .atomic)
print(outputURL.path)
