import '../slip_ocr.dart';
import 'bank_parser.dart';

class KbankParser implements BankParser {
  @override
  String get bankCode => 'KBANK';

  @override
  bool matches(String raw) =>
      raw.contains('K PLUS') || raw.contains('กสิกร') || raw.contains('K+');

  @override
  ParsedSlip parse(String raw, {List<OcrLine>? lines}) =>
      parseSlipFields(raw, minRefLength: 15, maxRefLength: 25, lines: lines);
}
