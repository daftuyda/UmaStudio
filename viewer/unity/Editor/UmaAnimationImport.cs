using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using UnityEditor;
using UnityEditor.Animations;
using UnityEngine;

// Runs in an isolated, temporary Unity project. Never opens or changes the user's project.
public static class UmaAnimationImport
{
    [Serializable] public class Bone { public string name; public int index, parent; public float[] p, q, s; }
    [Serializable] public class Request { public Bone[] bones; public string output; public int fps = 60; }
    [Serializable] public class Track { public string bone, type; public List<float> values = new List<float>(); }
    [Serializable] public class Result { public string name, error; public float duration; public int fps, frames; public bool loop; public List<Track> tracks = new List<Track>(); }

    public static void Run()
    {
        var args = Environment.GetCommandLineArgs();
        var path = args[Array.IndexOf(args, "-studioRequest") + 1];
        var request = JsonUtility.FromJson<Request>(File.ReadAllText(path));
        var result = new Result();
        GameObject root = null;
        Avatar avatar = null;
        try
        {
            AssetDatabase.ImportAsset("Assets/Input.anim", ImportAssetOptions.ForceSynchronousImport);
            var clip = AssetDatabase.LoadAssetAtPath<AnimationClip>("Assets/Input.anim");
            if (!clip) throw new Exception("Unity could not read this animation clip.");
            if (!clip.humanMotion) throw new Exception("This importer currently supports Unity Humanoid animations. This clip contains generic or legacy bone animation.");
            if (clip.length <= 0 || clip.length > 120) throw new Exception("Animation duration must be between 0 and 120 seconds.");
            root = new GameObject("UmaImportRoot");
            var transforms = new Dictionary<int, Transform>();
            foreach (var b in request.bones) transforms[b.index] = new GameObject(b.name).transform;
            foreach (var b in request.bones)
            {
                var t = transforms[b.index];
                t.SetParent(transforms.ContainsKey(b.parent) ? transforms[b.parent] : root.transform, false);
                t.localPosition = new Vector3(b.p[0], b.p[1], b.p[2]);
                t.localRotation = new Quaternion(b.q[0], b.q[1], b.q[2], b.q[3]);
                t.localScale = new Vector3(b.s[0], b.s[1], b.s[2]);
            }
            var mapping = new Dictionary<string, string> {
                {"Hips", "Hip"}, {"Spine", "Waist"}, {"Chest", "Spine"}, {"UpperChest", "Chest"},
                {"Neck", "Neck"}, {"Head", "Head"}
            };
            foreach (var side in new [] {"Left", "Right"})
            {
                var suffix = side == "Left" ? "_L" : "_R";
                foreach (var pair in new [] { "UpperLeg:Thigh", "LowerLeg:Knee", "Foot:Ankle", "Toes:Toe",
                    "Shoulder:Shoulder", "UpperArm:Arm", "LowerArm:Elbow", "Hand:Wrist" })
                {
                    var fields = pair.Split(':'); mapping[side + fields[0]] = fields[1] + suffix;
                }
                foreach (var finger in new [] {"Thumb", "Index", "Middle", "Ring", "Little"})
                {
                    var uma = finger == "Little" ? "Pinky" : finger;
                    var parts = new [] {"Proximal", "Intermediate", "Distal"};
                    for (int i = 0; i < 3; i++) mapping[side + finger + parts[i]] = uma + "_0" + (i + 1) + suffix;
                }
            }
            var names = new HashSet<string>(request.bones.Select(b => b.name));
            var human = mapping.Where(p => names.Contains(p.Value)).Select(p => new HumanBone {
                humanName = p.Key, boneName = p.Value, limit = new HumanLimit { useDefaultValues = true }
            }).ToArray();
            var skeleton = root.GetComponentsInChildren<Transform>().Select(t => new SkeletonBone {
                name = t.name, position = t.localPosition, rotation = t.localRotation, scale = t.localScale
            }).ToArray();
            avatar = AvatarBuilder.BuildHumanAvatar(root, new HumanDescription {
                human = human, skeleton = skeleton, upperArmTwist = .5f, lowerArmTwist = .5f,
                upperLegTwist = .5f, lowerLegTwist = .5f, armStretch = 0, legStretch = 0, feetSpacing = 0
            });
            if (!avatar.isValid || !avatar.isHuman) throw new Exception("This character's skeleton could not form a valid Humanoid Avatar.");
            var animator = root.AddComponent<Animator>();
            animator.avatar = avatar;
            animator.applyRootMotion = true;
            animator.cullingMode = AnimatorCullingMode.AlwaysAnimate;
            AssetDatabase.DeleteAsset("Assets/Import.controller");
            var controller = AnimatorController.CreateAnimatorControllerAtPath("Assets/Import.controller");
            var state = controller.layers[0].stateMachine.AddState("Import");
            state.motion = clip;
            controller.layers[0].stateMachine.defaultState = state;
            animator.runtimeAnimatorController = controller;
            animator.Rebind();
            animator.Update(0);
            var targets = request.bones.Where(b => mapping.Values.Contains(b.name)).ToArray();
            foreach (var b in targets)
            {
                result.tracks.Add(new Track { bone = b.name, type = "position" });
                result.tracks.Add(new Track { bone = b.name, type = "quaternion" });
            }
            var placement = transforms[request.bones.First(b => b.name == "Position").index];
            var placementParentInverse = placement.parent.worldToLocalMatrix;
            var placementParentRotation = Quaternion.Inverse(placement.parent.rotation);
            var placementPosition = new Track { bone = "Position", type = "position" };
            var placementRotation = new Track { bone = "Position", type = "quaternion" };
            result.tracks.Add(placementPosition);
            result.tracks.Add(placementRotation);
            result.name = clip.name;
            result.duration = Mathf.Round(clip.length * request.fps) / request.fps;
            result.fps = request.fps;
            result.frames = Mathf.RoundToInt(result.duration * request.fps) + 1;
            result.loop = AnimationUtility.GetAnimationClipSettings(clip).loopTime;
            // Absolute sampling includes the final frame and avoids accumulating root-motion deltas.
            AnimationMode.StartAnimationMode();
            for (int i = 0; i < result.frames; i++)
            {
                AnimationMode.BeginSampling();
                AnimationMode.SampleAnimationClip(root, clip, Mathf.Min(i / (float)request.fps, clip.length));
                AnimationMode.EndSampling();
                var rootP = placementParentInverse.MultiplyPoint3x4(placement.position);
                var rootQ = placementParentRotation * placement.rotation;
                placementPosition.values.AddRange(new [] {-rootP.x, rootP.y, rootP.z});
                placementRotation.values.AddRange(new [] {rootQ.x, -rootQ.y, -rootQ.z, rootQ.w});
                for (int j = 0; j < targets.Length; j++)
                {
                    var t = transforms[targets[j].index];
                    var p = t.localPosition;
                    var q = t.localRotation;
                    result.tracks[j * 2].values.AddRange(new [] {-p.x, p.y, p.z});
                    result.tracks[j * 2 + 1].values.AddRange(new [] {q.x, -q.y, -q.z, q.w});
                }
            }
            AnimationMode.StopAnimationMode();
            Debug.Log("UmaStudio sampled " + result.name + ": " + result.frames + " frames, human scale " + animator.humanScale);
        }
        catch (Exception e) { result.error = e.ToString(); Debug.LogException(e); }
        finally
        {
            if (AnimationMode.InAnimationMode()) AnimationMode.StopAnimationMode();
            if (root) UnityEngine.Object.DestroyImmediate(root);
            if (avatar) UnityEngine.Object.DestroyImmediate(avatar);
            File.WriteAllText(request.output, JsonUtility.ToJson(result));
            EditorApplication.Exit(string.IsNullOrEmpty(result.error) ? 0 : 1);
        }
    }
}
