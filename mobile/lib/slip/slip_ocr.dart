import 'package:google_mlkit_text_recognition/google_mlkit_text_recognition.dart';

/// OCR result from a slip image.
class SlipOcrResult {
  final String rawText;
  final double confidence;

  const SlipOcrResult({
    required this.rawText,
    required this.confidence,
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
    return SlipOcrResult(
      rawText: recognized.text,
      confidence: _averageConfidence(recognized),
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
