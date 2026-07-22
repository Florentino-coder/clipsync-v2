import 'bank_parser.dart';
import 'bbl_parser.dart';
import 'kbank_parser.dart';
import 'scb_parser.dart';

class ParserRegistry {
  static final parsers = <BankParser>[
    ScbParser(),
    KbankParser(),
    BblParser(),
  ];

  static (String, ParsedSlip) parseAny(String raw) {
    for (final parser in parsers) {
      if (parser.matches(raw)) {
        return (parser.bankCode, parser.parse(raw));
      }
    }
    return ('UNKNOWN', ParsedSlip(valid: false, errors: ['bank_unknown']));
  }
}
