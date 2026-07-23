// lib/main.dart

import 'package:flutter/material.dart';
import 'package:flutter_foreground_task/flutter_foreground_task.dart';

import 'clip_service.dart';
import 'home_screen.dart';
import 'license/license_gate.dart';

void main() {
  WidgetsFlutterBinding.ensureInitialized();
  FlutterForegroundTask.initCommunicationPort();
  initForegroundTask();
  runApp(const App());
}

/// Test APK builds (clipsync-v2 / applicationId `.test`) skip the license wall.
const bool kClipSyncTestBuild = bool.fromEnvironment(
  'CLIPSYNC_TEST_BUILD',
  defaultValue: false,
);

class App extends StatelessWidget {
  const App({super.key});

  @override
  Widget build(BuildContext context) {
    final home = kClipSyncTestBuild
        ? const HomeScreen()
        : const LicenseGate(child: HomeScreen());
    return WithForegroundTask(
      child: MaterialApp(
        title: 'ClipSync',
        debugShowCheckedModeBanner: false,
        theme: ThemeData(
          colorSchemeSeed: Colors.indigo,
          useMaterial3: true,
          brightness: Brightness.light,
        ),
        darkTheme: ThemeData(
          colorSchemeSeed: Colors.indigo,
          useMaterial3: true,
          brightness: Brightness.dark,
        ),
        home: home,
      ),
    );
  }
}
