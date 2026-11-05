// Headless test harness (WO-1.1, [QA:S2]): ./gradlew runServer -Pharness
// prepareHarness resets the scripted test world, runServer starts a dedicated dev server with
// -Dgregtec.harness=true (the mod exports, checks the files and shuts down), and checkHarness
// fails the build unless run/server/gregteclink/harness-result.json says "pass": true.
// Without -Pharness, runServer is the template's plain dev server.

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
        from(layout.projectDirectory.dir("harness"))
        into(serverDir)
    }

    val checkHarness = tasks.register<CheckHarness>("checkHarness") {
        description = "Fails unless the harness wrote a passing harness-result.json."
        resultFile.set(serverDir.file("gregteclink/harness-result.json"))
    }

    tasks.named<JavaExec>("runServer") {
        dependsOn(prepareHarness)
        jvmArgs("-Dgregtec.harness=true", "-Xmx6G")
        finalizedBy(checkHarness)
    }
}
