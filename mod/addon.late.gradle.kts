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
    val clientDir = layout.projectDirectory.dir("run/client")
    val neiFail = providers.gradleProperty("harnessNeiFail").isPresent

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
        from(layout.projectDirectory.dir("harness/config")) {
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
            finalizedBy(checkClientHarness, removeClientHarnessConfig)
        }
    }
}
