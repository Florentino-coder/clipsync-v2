import 'package:clipsync_app/slip/slip_ocr.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  test('OcrLine sorts by yTop ascending', () {
    final lines = [
      const OcrLine(text: 'bottom', yTop: 300),
      const OcrLine(text: 'top', yTop: 10),
      const OcrLine(text: 'mid', yTop: 100),
    ]..sort((a, b) => a.yTop.compareTo(b.yTop));
    expect(lines.map((e) => e.text).toList(), ['top', 'mid', 'bottom']);
  });

  test('linesFromRawText assigns synthetic Y by row index', () {
    final lines = linesFromRawText('a\nb\nc');
    expect(lines.map((e) => e.text).toList(), ['a', 'b', 'c']);
    expect(lines[0].yTop, lessThan(lines[1].yTop));
    expect(lines[1].yTop, lessThan(lines[2].yTop));
  });

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
