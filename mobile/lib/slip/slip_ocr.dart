import 'package:google_mlkit_text_recognition/google_mlkit_text_recognition.dart';

class OcrLine {
  final String text;
  final int yTop;
  const OcrLine({required this.text, required this.yTop});
}

List<OcrLine> linesFromRawText(String raw) {
  final out = <OcrLine>[];
  final rows = raw.split('\n');
  for (var i = 0; i < rows.length; i++) {
    final t = rows[i].trim();
    if (t.isEmpty) continue;
    out.add(OcrLine(text: t, yTop: i * 100));
  }
  return out;
}

/// OCR result from a slip image.
class SlipOcrResult {
  final String rawText;
  final double confidence;
  final List<OcrLine> lines;

  const SlipOcrResult({
    required this.rawText,
    required this.confidence,
    this.lines = const [],
  });
}

/// Abstraction over slip OCR so pipeline tests avoid ML Kit.
abstract class SlipOcr {
  Future<SlipOcrResult> run(String imagePath);
}

/// ML Kit Latin text recognition for slip images.
class MlKitSlipOcr implements SlipOcr {
  MlKitSlipOcr({TextRecognizer? recognizer})
      : _recognizer = recognizer ??
            TextRecognizer(script: TextRecognitionScript.latin);

  final TextRecognizer _recognizer;

  @override
  Future<SlipOcrResult> run(String imagePath) async {
    final inputImage = InputImage.fromFilePath(imagePath);
    final recognized = await _recognizer.processImage(inputImage);
    final lineBoxes = <OcrLine>[];
    for (final block in recognized.blocks) {
      for (final line in block.lines) {
        final top = line.boundingBox?.top;
        if (top == null) continue;
        lineBoxes.add(OcrLine(text: line.text, yTop: top.round()));
      }
    }
    lineBoxes.sort((a, b) => a.yTop.compareTo(b.yTop));
    return SlipOcrResult(
      rawText: recognized.text,
      confidence: _averageConfidence(recognized),
      lines: lineBoxes.isEmpty ? linesFromRawText(recognized.text) : lineBoxes,
    );
  }

  Future<void> close() => _recognizer.close();

  static double _averageConfidence(RecognizedText recognized) {
    final confidences = <double>[];
    for (final block in recognized.blocks) {
      for (final line in block.lines) {
        for (final element in line.elements) {
          final value = element.confidence;
          if (value != null) {
            confidences.add(value);
          }
        }
      }
    }

    return averageConfidenceFromValues(
      textEmpty: recognized.text.isEmpty,
      confidences: confidences,
    );
  }

  /// Package-visible helper for unit tests.
  ///
  /// Returns `0.0` when text is empty or no element confidences were reported
  /// (unknown confidence must not look like a perfect OCR score).
  static double averageConfidenceFromValues({
    required bool textEmpty,
    required List<double> confidences,
  }) {
    if (textEmpty) {
      return 0.0;
    }
    if (confidences.isEmpty) {
      return 0.0;
    }
    return confidences.reduce((a, b) => a + b) / confidences.length;
  }
}
