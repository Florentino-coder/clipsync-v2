import 'package:clipsync_app/slip/slip_ocr.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  group('MlKitSlipOcr.averageConfidenceFromValues', () {
    test('returns 0 when text is empty', () {
      expect(
        MlKitSlipOcr.averageConfidenceFromValues(
          textEmpty: true,
          confidences: [0.9],
        ),
        0.0,
      );
    });

    test('returns 0 when confidences are unknown/empty', () {
      expect(
        MlKitSlipOcr.averageConfidenceFromValues(
          textEmpty: false,
          confidences: const [],
        ),
        0.0,
      );
    });

    test('averages reported element confidences', () {
      expect(
        MlKitSlipOcr.averageConfidenceFromValues(
          textEmpty: false,
          confidences: [0.5, 1.0],
        ),
        0.75,
      );
    });
  });
}
