import 'package:flutter/material.dart';

import 'app_model.dart';
import 'ui/root.dart';

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();
  final model = AppModel();
  runApp(KonnaDexApp(model: model));
  model.init();
}

class KonnaDexApp extends StatelessWidget {
  const KonnaDexApp({super.key, required this.model});
  final AppModel model;

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: '포켓몬 도감',
      debugShowCheckedModeBanner: false,
      theme: ThemeData(colorSchemeSeed: const Color(0xFFE53935), useMaterial3: true),
      home: RootScreen(model: model),
    );
  }
}
