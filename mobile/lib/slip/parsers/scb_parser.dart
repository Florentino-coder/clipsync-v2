import '../slip_ocr.dart';
import 'bank_parser.dart';

class ScbParser implements BankParser {
  @override
  String get bankCode => 'SCB';

  @override
  bool matches(String raw) =>
      raw.contains('SCB') ||
      raw.contains('Siam Commercial') ||
      raw.contains('ไทยพาณิชย์');

  @override
  ParsedSlip parse(String raw, {List<OcrLine>? lines}) =>
      parseSlipFields(raw, minRefLength: 15, maxRefLength: 25, lines: lines);
}
