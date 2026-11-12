// Headless test harness (WO-1.1, [QA:S2]): ./gradlew runServer -Pharness
// prepareHarness resets the scripted test world, runServer starts a dedicated dev server with
// -Dgregtec.harness=true (the mod exports, checks the files and shuts down), and checkHarness
// fails the build unless run/server/gregteclink/harness-result.json says "pass": true;
// removeHarnessConfig then deletes run/server/config/gregteclink.cfg.
// The client harness (runClient25 / runClient -Pharness) is at the end of this file.
// Without -Pharness, runServer and runClient are the template's plain dev runs.

abstract class CheckHarness : DefaultTask() {
    @get:Internal
    abstract val resultFile: RegularFileProperty

    init {
        outputs.upToDateWhen { false }
    }

    @TaskAction
    fun check() {
        val f = resultFile.get().asFile
        if (!f.isFile) {
            throw GradleException("harness: $f is missing: the server crashed, hung or never reached server start")
        }
        @Suppress("UNCHECKED_CAST")
        val result = groovy.json.JsonSlurper().parse(f) as Map<String, Any?>
        @Suppress("UNCHECKED_CAST")
        val checks = result["checks"] as List<Map<String, Any?>>
        for (c in checks) {
            logger.lifecycle("${if (c["ok"] == true) "PASS" else "FAIL"} ${c["name"]}: ${c["detail"]}")
        }
        val failed = checks.filter { it["ok"] != true }.map { it["name"] }
        if (result["pass"] != true) {
            throw GradleException("harness failed (run ${result["run_id"]}): $failed; see $f")
        }
        logger.lifecycle("harness passed: ${checks.size} checks, run ${result["run_id"]}")
    }
}

if (providers.gradleProperty("harness").isPresent) {
    val serverDir = layout.projectDirectory.dir("run/server")

    val cleanHarness = tasks.register<Delete>("cleanHarness") {
        description = "Deletes the harness world and earlier GregtecLink output from run/server."
        // Deleting the world also avoids FML's "/fml confirm" stall after a mod-set change.
        delete(serverDir.dir("harness"), serverDir.dir("gregteclink"))
    }

    val prepareHarness = tasks.register<Copy>("prepareHarness") {
        description = "Copies harness/ (eula, server.properties, gregteclink.cfg) into run/server."
        dependsOn(cleanHarness)
        from(layout.projectDirectory.dir("harness")) {
            exclude("client/**", "client-nei-fail/**")
        }
        into(serverDir)
    }

    val checkHarness = tasks.register<CheckHarness>("checkHarness") {
        description = "Fails unless the harness wrote a passing harness-result.json."
        resultFile.set(serverDir.file("gregteclink/harness-result.json"))
    }

    val removeHarnessConfig = tasks.register<Delete>("removeHarnessConfig") {
        description = "Deletes the harness's export=true config again, so a later plain runServer exports nothing."
        delete(serverDir.file("config/gregteclink.cfg"))
    }

    tasks.named<JavaExec>("runServer") {
        dependsOn(prepareHarness)
        jvmArgs("-Dgregtec.harness=true", "-Xmx6G")
        finalizedBy(checkHarness, removeHarnessConfig)
    }

    // Client harness (PR 1b): ./gradlew runClient25 -Pharness (or runClient), under Xvfb in CI. With autoexport on,
    // the mod opens its own world from the main menu, exports, runs NEI's dump once NEI has loaded, checks the run
    // and quits. Seeds: an old marked autoexport world (rebuilt) and a marked decoy world (left alone).
    // Add -PharnessNeiFail to make dumps/recipes a file: NEI's dump then fails, and the harness expects
    // "nei": "failed" in a run that is still complete.
    // Add -PharnessNeiHandlerFail to give NEI one more recipe handler, which always fails, for the NEI step: the
    // harness then expects "nei": "ok" with part_warnings.nei for it (D188). Add -PharnessNeiLookupFirst to look up an
    // item's recipes, as R does, just before the NEI step: the harness then expects the dump to have built GT's NEI
    // recipe caches again (D188). CI runs these two together, after the integration tests.
    // Add -PharnessClientLink to turn the client module on as well (WO-8.1): after the export, still in the world, the
    // harness looks at 4 placed blocks and hovers over 6 items, presses What's this? for each and checks the answers
    // of the real core that tests/harness/client_link_core.py runs on 127.0.0.1:18765 (start it first; it restarts
    // the core once when the harness asks, so the reconnect is checked too).
    val clientDir = layout.projectDirectory.dir("run/client")
    val neiFail = providers.gradleProperty("harnessNeiFail").isPresent
    val neiHandlerFail = providers.gradleProperty("harnessNeiHandlerFail").isPresent
    val neiLookupFirst = providers.gradleProperty("harnessNeiLookupFirst").isPresent
    val clientLink = providers.gradleProperty("harnessClientLink").isPresent

    val cleanClientHarness = tasks.register<Delete>("cleanClientHarness") {
        description = "Deletes the client harness's worlds, NEI dump and earlier GregtecLink output from run/client."
        delete(
            clientDir.dir("saves/gregteclink-autoexport"),
            clientDir.dir("saves/gregteclink-harness-decoy"),
            clientDir.dir("gregteclink"),
            clientDir.dir("dumps"),
        )
    }

    val prepareClientHarness = tasks.register<Copy>("prepareClientHarness") {
        description = "Copies harness/config/gregteclink.cfg and the seeded worlds in harness/client/ into run/client."
        dependsOn(cleanClientHarness)
        from(layout.projectDirectory.dir(if (clientLink) "harness/config-client-link" else "harness/config")) {
            into("config")
        }
        from(layout.projectDirectory.dir("harness/client"))
        // Not kept under a dumps/ path in git: the repository ignores every dumps/ folder.
        if (neiFail) {
            from(layout.projectDirectory.file("harness/client-nei-fail/recipes")) {
                into("dumps")
            }
        }
        into(clientDir)
    }

    val checkClientHarness = tasks.register<CheckHarness>("checkClientHarness") {
        description = "Fails unless the client harness wrote a passing harness-result.json."
        resultFile.set(clientDir.file("gregteclink/harness-result.json"))
    }

    val removeClientHarnessConfig = tasks.register<Delete>("removeClientHarnessConfig") {
        description = "Deletes the client harness's export=true config again, so a later plain runClient exports nothing."
        delete(clientDir.file("config/gregteclink.cfg"))
    }

    listOf("runClient", "runClient25").forEach { name ->
        tasks.named<JavaExec>(name) {
            dependsOn(prepareClientHarness)
            jvmArgs("-Dgregtec.harness=true", "-Dgregtec.autoexport=true", "-Xmx6G")
            if (neiFail) jvmArgs("-Dgregtec.harness.neiFail=true")
            if (neiHandlerFail) jvmArgs("-Dgregtec.harness.neiHandlerFail=true")
            if (neiLookupFirst) jvmArgs("-Dgregtec.harness.neiLookupFirst=true")
            if (clientLink) jvmArgs("-Dgregtec.harness.clientLink=true")
            // lwjgl3ify writes a .desktop entry under $XDG_DATA_HOME (else ~/.local/share) and refuses to
            // start when neither exists, as on CI runners. Point it inside run/client instead.
            val xdgData = clientDir.dir("xdg-data").asFile
            environment("XDG_DATA_HOME", xdgData.absolutePath)
            doFirst { xdgData.mkdirs() }
            // The configuration cache keeps the environment the task had when it was configured, so a
            // second xvfb-run would hand the game the first one's DISPLAY and its deleted XAUTHORITY
            // ("Can't connect to X11 window server"). Take the display variables from this run instead.
            doFirst {
                val exec = this as JavaExec
                for (variable in listOf("DISPLAY", "XAUTHORITY", "WAYLAND_DISPLAY")) {
                    val now = System.getenv(variable)
                    if (now != null) exec.environment(variable, now) else exec.environment.remove(variable)
                }
            }
            finalizedBy(checkClientHarness, removeClientHarnessConfig)
        }
    }
}
